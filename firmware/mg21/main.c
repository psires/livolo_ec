/* MG21 board wiring is selected explicitly in ec-mg21-board.h. */
#include "ec-mg21-platform.h"
#include "ec-runtime.h"
#include "ec-uart-config.h"
#include "em_chip.h"
#include "em_cmu.h"
#include "em_gpio.h"
#include "em_usart.h"
#include "em_ldma.h"
#include "ec-uart-rx.h"
#include "rail_ieee802154.h"
#include "pa_conversions_efr32.h"
#include "api/application_properties.h"
#include "api/btl_reset_info.h"
#include "../version.h"
#include <stdio.h>
#include <string.h>

#define UART_SIZE 8192u
static uint8_t uart_tx[UART_SIZE];
static volatile unsigned uart_head, uart_tail;
static volatile unsigned long uart_lost;
static int (*uart_input)(unsigned char);
static volatile uint32_t milliseconds;
static RAIL_Handle_t radio;
static volatile int tx_done, tx_result;
static volatile unsigned cal_needed;
static unsigned use_cca;
static uint8_t tx_fifo[256] __attribute__((aligned(4)));
static uint8_t rx_fifo[2048] __attribute__((aligned(4)));
static volatile unsigned long rf_drops;
static volatile unsigned long uart_rx_errors;
static volatile unsigned long uart_rx_overruns;
static volatile unsigned long uart_framing_errors;
static volatile unsigned long uart_parity_errors;
static unsigned long uart_dma_errors;
/* RAIL DMA is opt-in via RAIL_UseDma; this application does not opt in.
 * Channel 0 and the LDMA engine belong exclusively to this receive ring.
 */
#define UART_DMA_CHANNEL 0
#define UART_DMA_BLOCK 2048u
static volatile uint8_t uart_rx[EC_UART_RX_SIZE] __attribute__((aligned(4)));
static LDMA_Descriptor_t uart_rx_desc[4];
static ec_uart_rx_t uart_rx_state;
static const LDMA_TransferCfg_t uart_rx_cfg =
	LDMA_TRANSFER_CFG_PERIPHERAL(ldmaPeripheralSignal_USART0_RXDATAV);

void ec_mg21_status(char *out, size_t capacity)
{
	snprintf(out, capacity,
		 " radio_drops=%lu uart_rx_errors=%lu uart_rx_overruns=%lu "
		 "uart_framing_errors=%lu uart_parity_errors=%lu "
		 "uart_rx_dma_errors=%lu uart_rx_resyncs=%lu "
		 "uart_rx_highwater=%u "
		 "uart_rx_poll_gap_us=%lu",
		 rf_drops, uart_rx_errors, uart_rx_overruns,
		 uart_framing_errors, uart_parity_errors, uart_dma_errors,
		 (unsigned long)uart_rx_state.resyncs, uart_rx_state.highwater,
		 (unsigned long)uart_rx_state.max_gap_us);
}

void ec_mg21_bootloader(void)
{
	uint32_t start = clock_time();
	volatile BootloaderResetCause_t *cause = (void *)0x20000000;

	while ((uart_head != uart_tail ||
		!(USART0->STATUS & USART_STATUS_TXC)) &&
	       (uint32_t)(clock_time() - start) < 1000)
		;
	__disable_irq();
	cause->reason = BOOTLOADER_RESET_REASON_BOOTLOAD;
	cause->signature = BOOTLOADER_RESET_SIGNATURE_VALID;
	__DSB();
	NVIC_SystemReset();
}

/* Gecko finds this through vector slot 13. No secure-boot signature claimed. */
const ApplicationProperties_t sl_app_properties = {
	.magic = APPLICATION_PROPERTIES_MAGIC,
	.structVersion = APPLICATION_PROPERTIES_VERSION,
	.signatureType = APPLICATION_SIGNATURE_NONE,
	.app = { .type = APPLICATION_TYPE_FLEX,
		 .version = (EC_VERSION_MAJOR << 16) | EC_VERSION_MINOR,
		 .productId = { 'L', 'I', 'V', 'O', 'L', 'O', '-', 'M', 'G',
				'2', '1' } },
};

/** @brief Advance the lease/status clock independently of the radio timer. */
void SysTick_Handler(void)
{
	milliseconds++;
}

clock_time_t clock_time(void)
{
	return milliseconds;
}

unsigned ec_uart_free(void)
{
	return (uart_tail - uart_head - 1) & (UART_SIZE - 1);
}

unsigned long ec_uart_dropped(void)
{
	return uart_lost;
}

int uart0_write(const void *data, size_t length)
{
	const uint8_t *bytes = data;
	unsigned head = uart_head;

	if (length > ec_uart_free()) {
		uart_lost += length;
		return -1;
	}
	/* Only the main loop produces TX; the IRQ only advances the tail.
	 * Keep RX interrupts enabled while staging a whole log/reply. Publishing
	 * the head once prevents the TX IRQ from observing a partial write.
	 */
	for (size_t i = 0; i < length; i++) {
		uart_tx[head] = bytes[i];
		head = (head + 1) & (UART_SIZE - 1);
	}
	__DMB();
	uart_head = head;
	/* Re-enable after publication even if the IRQ drained the old queue. */
	USART_IntEnable(USART0, USART_IEN_TXBL);
	return (int)length;
}

void uart0_set_callback(int (*callback)(unsigned char))
{
	uart_input = callback;
}

/** @brief Drain the nonblocking UART output queue at wire speed. */
void USART0_TX_IRQHandler(void)
{
	while ((USART0->STATUS & USART_STATUS_TXBL) && uart_tail != uart_head) {
		USART0->TXDATA = uart_tx[uart_tail];
		uart_tail = (uart_tail + 1) & (UART_SIZE - 1);
	}
	if (uart_tail == uart_head)
		USART_IntDisable(USART0, USART_IEN_TXBL);
}

/** @brief Start continuous linked DMA, with no per-byte or per-block IRQ.
 * The USART FIFO is drained even inside RAIL critical sections. Descriptor
 * boundaries do not require CPU intervention. The main loop checks sticky
 * hardware faults before and after staging bytes for the command parser.
 */
static void uart_rx_start(void)
{
	LDMA_StartTransfer(UART_DMA_CHANNEL, &uart_rx_cfg, uart_rx_desc);
	LDMA->IEN = 0;
}

/** @brief Start a continuous four-descriptor DMA ring with a fresh RX cursor. */
static void uart_rx_init(void)
{
	LDMA_Init_t init = LDMA_INIT_DEFAULT;
	LDMA_Init(&init);
	NVIC_DisableIRQ(LDMA_IRQn);
	for (unsigned i = 0; i < 4; i++) {
		uart_rx_desc[i] =
			(LDMA_Descriptor_t)LDMA_DESCRIPTOR_LINKREL_P2M_BYTE(
				&USART0->RXDATA, &uart_rx[i * UART_DMA_BLOCK],
				UART_DMA_BLOCK, i == 3 ? -3 : 1);
		uart_rx_desc[i].xfer.doneIfs = 0;
	}
	USART0->CMD = USART_CMD_CLEARRX;
	USART_IntClear(USART0, _USART_IF_MASK);
	ec_uart_rx_init(&uart_rx_state, RAIL_GetTime());
	uart_rx_start();
}

/** @brief Validate a DMA pointer snapshot against hardware errors and laps.
 * @return One for a valid snapshot, zero after loss and resynchronization.
 */
static int uart_rx_observe(void)
{
	uint32_t before = RAIL_GetTime();
	/* Sample position before faults, so every staged byte is covered by the
	 * subsequent sticky-error read, including errors concurrent with DMA.
	 */
	unsigned head = (LDMA->CH[UART_DMA_CHANNEL].DST - (uintptr_t)uart_rx) &
			(EC_UART_RX_SIZE - 1u);
	__DMB();
	uint32_t flags = USART_IntGet(USART0) &
			 (USART_IF_RXOF | USART_IF_FERR | USART_IF_PERR);
	int fault = flags != 0;

	USART_IntClear(USART0, flags);
	if (flags)
		uart_rx_errors++;
	if (flags & USART_IF_RXOF)
		uart_rx_overruns++;
	if (flags & USART_IF_FERR)
		uart_framing_errors++;
	if (flags & USART_IF_PERR)
		uart_parity_errors++;
	if (LDMA->IF & LDMA_IF_ERROR) {
		uart_dma_errors++;
		LDMA_StopTransfer(UART_DMA_CHANNEL);
		LDMA_IntClear(LDMA_IF_ERROR);
		uart_rx_start();
		head = 0;
		fault = 1;
	}
	/* DST is the next write address (xG21 RM 25.7.27). At a link boundary
	 * it can equal the end of the ring; reduce that to offset zero.
	 */
	return ec_uart_rx_observe(&uart_rx_state, head, before, RAIL_GetTime(),
				  EC_UART_BAUD, fault);
}

/** @brief Stage bounded input, revalidate it, then feed the line collector.
 * A hardware error or possible ring lap poisons the partial line through its
 * next newline. Never execute a command assembled from bytes around a loss.
 */
static void uart_rx_poll(void)
{
	uint8_t copy[EC_UART_RX_CHUNK];
	unsigned length;
	if (!uart_rx_observe()) {
		if (uart_input)
			uart_input(0);
		return;
	}
	length = ec_uart_rx_copy(&uart_rx_state, uart_rx, copy);
	if (!length)
		return;
	if (!uart_rx_observe()) {
		if (uart_input)
			uart_input(0);
		return;
	}
	ec_uart_rx_consume(&uart_rx_state, length);
	for (unsigned i = 0; i < length; i++)
		if (uart_input)
			uart_input(copy[i]);
}

/** @brief Initialize the board's UART without CTS/RTS flow control. */
static void uart_init(void)
{
	USART_InitAsync_TypeDef cfg = USART_INITASYNC_DEFAULT;

	CMU_ClockEnable(cmuClock_GPIO, true);
	CMU_ClockEnable(cmuClock_USART0, true);
	GPIO_PinModeSet(gpioPortB, 1, gpioModePushPull, 1);
	GPIO_PinModeSet(gpioPortB, 0, gpioModeInputPull, 1);
#if EC_BOARD_HAS_RTS_PD4
	/* ZB-GW04 v1.2 connects RTS to CH340; Sonoff does not. */
	GPIO_PinModeSet(gpioPortD, 4, gpioModePushPull, 0);
#endif
	cfg.baudrate = EC_UART_BAUD;
	USART_InitAsync(USART0, &cfg);
	GPIO->USARTROUTE[0].TXROUTE =
		(gpioPortB << _GPIO_USART_TXROUTE_PORT_SHIFT) |
		(1 << _GPIO_USART_TXROUTE_PIN_SHIFT);
	GPIO->USARTROUTE[0].RXROUTE =
		(gpioPortB << _GPIO_USART_RXROUTE_PORT_SHIFT);
	GPIO->USARTROUTE[0].ROUTEEN =
		GPIO_USART_ROUTEEN_TXPEN | GPIO_USART_ROUTEEN_RXPEN;
	USART_IntClear(USART0, _USART_IF_MASK);
	NVIC_SetPriority(USART0_TX_IRQn, 7);
	NVIC_EnableIRQ(USART0_TX_IRQn);
}

/** @brief Hold RX packets and record TX completion without UART work in ISR.
 * @param handle RAIL instance which raised the event.
 * @param events Bitmask of radio events.
 */
static void radio_event(RAIL_Handle_t handle, RAIL_Events_t events)
{
	if (events & RAIL_EVENT_RX_PACKET_RECEIVED)
		RAIL_HoldRxPacket(handle);
	if (events & RAIL_EVENT_RX_FIFO_OVERFLOW)
		rf_drops++;
	if (events & RAIL_EVENT_CAL_NEEDED)
		cal_needed = 1;
	if (events & RAIL_EVENTS_TX_COMPLETION) {
		tx_result = (events & RAIL_EVENT_TX_PACKET_SENT) ? RADIO_TX_OK
			    : (events & RAIL_EVENT_TX_CHANNEL_BUSY)
				    ? RADIO_TX_COLLISION
				    : RADIO_TX_ERR;
		tx_done = 1;
	}
}

/** @brief Configure the send policy selected by the shared gateway core.
 * @param parameter Must be RADIO_PARAM_TX_MODE.
 * @param value Zero for immediate ACKs, or RADIO_TX_MODE_SEND_ON_CCA.
 * @return Zero on success, -1 for an unknown parameter.
 */
static int radio_setting(int parameter, int value)
{
	if (parameter != RADIO_PARAM_TX_MODE)
		return -1;
	use_cca = value != 0;
	return 0;
}

/** @brief Send one raw frame and wait boundedly with IRQs enabled.
 * @param data MAC frame without PHR/FCS; borrowed until return.
 * @param length MAC bytes, 1 through 125.
 * @return RADIO_TX_OK only on confirmed completion; collision or error otherwise.
 */
static int radio_send(const void *data, unsigned length)
{
	uint8_t packet[126];
	RAIL_Status_t result;
	RAIL_CsmaConfig_t csma = RAIL_CSMA_CONFIG_SINGLE_CCA;
	uint32_t started;

	if (!length || length > 125)
		return RADIO_TX_ERR;
	packet[0] = length + 2; /* PHR includes hardware-generated FCS. */
	memcpy(packet + 1, data, length);
	RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
	if (RAIL_WriteTxFifo(radio, packet, length + 1, true) != length + 1) {
		RAIL_StartRx(radio, 11, NULL);
		return RADIO_TX_ERR;
	}
	tx_done = 0;
	tx_result = RADIO_TX_ERR;
	started = RAIL_GetTime();
	if (use_cca)
		result = RAIL_StartCcaCsmaTx(radio, 11, RAIL_TX_OPTIONS_DEFAULT,
					     &csma, NULL);
	else
		result = RAIL_StartTx(radio, 11, RAIL_TX_OPTIONS_DEFAULT, NULL);
	if (result == RAIL_STATUS_NO_ERROR) {
		while (!tx_done &&
		       (uint32_t)(RAIL_GetTime() - started) < 20000u)
			; /* Radio IRQs complete TX; never called from an interrupt. */
	}
	if (!tx_done)
		RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
	RAIL_StartRx(radio, 11, NULL);
	return tx_done ? tx_result : RADIO_TX_ERR;
}

/** @brief Stop reception and any active radio transaction.
 * @return Zero after requesting idle.
 */
static int radio_off(void)
{
	RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
	return 0;
}

const struct ec_mg21_radio NETSTACK_RADIO = { radio_setting, radio_send,
					      radio_off };

/** @brief Fail closed and emit the failing SDK initialization status.
 * @param status SDK status; zero permits startup to continue.
 * @param stage Static name of the initialization operation.
 */
static void require_ok(RAIL_Status_t status, const char *stage)
{
	char line[100];
	int n;

	if (status == RAIL_STATUS_NO_ERROR)
		return;
	n = snprintf(line, sizeof(line), "FATAL stage=%s status=%u\n", stage,
		     status);
	uart0_write(line, n);
	for (;;)
		__WFI();
}

/** @brief Configure raw channel-11 reception and explicitly disable IEEE ACKs. */
static void radio_init(void)
{
	static RAIL_Config_t cfg = { .eventsCallback = radio_event };
	RAIL_IEEE802154_Config_t ieee = {
		.ackConfig = { .enable = false,
			       .rxTransitions = { RAIL_RF_STATE_RX,
						  RAIL_RF_STATE_RX },
			       .txTransitions = { RAIL_RF_STATE_RX,
						  RAIL_RF_STATE_RX } },
		.timings = { .idleToRx = 100,
			     .idleToTx = 100,
			     .rxToTx = 192,
			     .txToRx = 192 },
		.framesMask = RAIL_IEEE802154_ACCEPT_STANDARD_FRAMES,
		.promiscuousMode = true,
	};
	RAIL_TxPowerConfig_t power = { .mode = RAIL_TX_POWER_MODE_2P4GIG_HP,
				       .voltage = 3300,
				       .rampTime = 10 };
	RAIL_StateTransitions_t transitions = { RAIL_RF_STATE_RX,
						RAIL_RF_STATE_RX };
	uint16_t rx_size = sizeof(rx_fifo);

	radio = RAIL_Init(&cfg, NULL);
	require_ok(radio ? RAIL_STATUS_NO_ERROR : RAIL_STATUS_INVALID_STATE,
		   "init");
	require_ok(RAIL_IEEE802154_Config2p4GHzRadio(radio), "phy");
	require_ok(RAIL_IEEE802154_Init(radio, &ieee), "ieee");
	require_ok(RAIL_ConfigRxOptions(radio, RAIL_RX_OPTION_STORE_CRC, 0),
		   "crc");
	require_ok(RAIL_SetRxFifo(radio, rx_fifo, &rx_size), "rx_fifo");
	require_ok(RAIL_SetTxFifo(radio, tx_fifo, 0, sizeof(tx_fifo)) ==
				   sizeof(tx_fifo)
			   ? RAIL_STATUS_NO_ERROR
			   : RAIL_STATUS_INVALID_STATE,
		   "tx_fifo");
	require_ok(RAIL_InitTxPowerCurvesAlt(&RAIL_TxPowerCurvesVbat),
		   "curves");
	require_ok(RAIL_ConfigTxPower(radio, &power), "power_config");
	require_ok(RAIL_SetTxPowerDbm(radio, 0), "power");
	require_ok(RAIL_SetRxTransitions(radio, &transitions),
		   "rx_transitions");
	require_ok(RAIL_SetTxTransitions(radio, &transitions),
		   "tx_transitions");
	require_ok(RAIL_ConfigCal(radio, RAIL_CAL_ALL), "cal");
	require_ok(RAIL_ConfigEvents(radio, RAIL_EVENTS_ALL,
				     RAIL_EVENT_RX_PACKET_RECEIVED |
					     RAIL_EVENT_RX_FIFO_OVERFLOW |
					     RAIL_EVENTS_TX_COMPLETION |
					     RAIL_EVENT_CAL_NEEDED),
		   "events");
	require_ok(RAIL_StartRx(radio, 11, NULL), "rx");
}

/** @brief Deliver a held frame before processing host commands or logging. */
static void radio_poll(void)
{
	RAIL_RxPacketInfo_t info;
	RAIL_RxPacketDetails_t details;
	RAIL_RxPacketHandle_t packet;
	uint8_t bytes[128];

	packet = RAIL_GetRxPacketInfo(
		radio, RAIL_RX_PACKET_HANDLE_OLDEST_COMPLETE, &info);
	if (packet != RAIL_RX_PACKET_HANDLE_INVALID) {
		if (info.packetStatus == RAIL_RX_PACKET_READY_SUCCESS &&
		    info.packetBytes >= 2 && info.packetBytes <= 126 &&
		    RAIL_GetRxPacketDetailsAlt(radio, packet, &details) ==
			    RAIL_STATUS_NO_ERROR) {
			RAIL_CopyRxPacket(bytes, &info);
			RAIL_ReleaseRxPacket(radio, packet);
			/* With STORE_CRC off, RAIL retains PHR but strips two FCS bytes. */
			if (!(bytes[0] & 0x80) &&
			    bytes[0] == info.packetBytes + 1)
				ec_receive(bytes + 1, info.packetBytes - 1,
					   details.rssi, details.lqi);
			else
				rf_drops++;
		} else {
			RAIL_ReleaseRxPacket(radio, packet);
			rf_drops++;
		}
	}
	if (cal_needed) {
		cal_needed = 0;
		if (RAIL_Calibrate(radio, NULL, RAIL_CAL_ALL_PENDING) !=
		    RAIL_STATUS_NO_ERROR)
			rf_drops++;
	}
}

/** @brief Initialize board clocks, then run the portable UART/radio gateway.
 * @return Never returns.
 */
int main(void)
{
	CMU_HFXOInit_TypeDef hfxo = CMU_HFXOINIT_DEFAULT;
	char line[160];
	int n;

	CHIP_Init();
	hfxo.ctuneXiAna = EC_BOARD_HFXO_CTUNE;
	hfxo.ctuneXoAna = EC_BOARD_HFXO_CTUNE;
	CMU_HFXOInit(&hfxo);
	CMU_ClockSelectSet(cmuClock_SYSCLK, cmuSelect_HFXO);
	SystemCoreClockUpdate();
	SysTick_Config(SystemCoreClock / CLOCK_SECOND);
	uart_init();
	n = snprintf(line, sizeof(line),
		     EC_BOARD_MARKER " part=%08lx mem=%08lx core_hz=%lu\n",
		     (unsigned long)DEVINFO->PART,
		     (unsigned long)DEVINFO->MEMINFO,
		     (unsigned long)SystemCoreClock);
	uart0_write(line, n);
	radio_init();
	uart_rx_init();
	ec_gateway_start();
	for (;;) {
		radio_poll();
		uart_rx_poll();
		ec_gateway_poll();
	}
}
