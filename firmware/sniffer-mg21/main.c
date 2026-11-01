/* MG21 board wiring is selected explicitly in ec-mg21-board.h. */
#include "ec-mg21-platform.h"
#include "sniffer-wire.h"
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

__attribute__((section(".fw_version"), used)) const char ec_build_version[] =
	"EC-FW-VERSION=" EC_VERSION;

#define UART_SIZE 8192u
static uint8_t uart_tx[UART_SIZE];
static volatile unsigned uart_head, uart_tail;
static volatile unsigned long uart_lost;
static int (*uart_input)(unsigned char);
static volatile uint32_t milliseconds;
static RAIL_Handle_t radio;
static volatile unsigned cal_needed;
static uint8_t rx_fifo[8192] __attribute__((aligned(4)));
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

/** @brief Enter the preserved Gecko bootloader after bounded UART drain. */
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

/** @brief Read the maintenance-command clock.
 * @return Milliseconds since boot modulo 2^32.
 */
clock_time_t clock_time(void)
{
	return milliseconds;
}

/** @brief Read available space in the single-producer UART TX ring.
 * @return Number of bytes that can be queued without dropping a record.
 */
unsigned ec_uart_free(void)
{
	return (uart_tail - uart_head - 1) & (UART_SIZE - 1);
}

/** @brief Read the UART software-loss counter.
 * @return Bytes rejected because the UART TX ring was full, modulo ULONG_MAX+1.
 */
unsigned long ec_uart_dropped(void)
{
	return uart_lost;
}

/** @brief Queue a whole record or reject it without a partial write.
 * @param data Borrowed bytes copied before return.
 * @param length Number of readable bytes.
 * @return Queued byte count, or -1 on insufficient space. */
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

/** @brief Install a main-loop command-byte handler.
 * @param callback Handler receiving one UART byte. */
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

/** @brief Start the existing bounded DMA receive ring for maintenance commands. */
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

static uint32_t record_sequence, bad_packets, time_errors, cal_errors;
static uint32_t clock_low;
static uint64_t clock_extended;

/** @brief Extend RAIL's wrapping microsecond clock from frequent main-loop reads.
 * @return Monotonic radio microseconds since startup. */
static uint64_t radio_time(void)
{
	uint32_t now = RAIL_GetTime();

	clock_extended += (uint32_t)(now - clock_low);
	clock_low = now;
	return clock_extended;
}

/** @brief Emit one atomic binary record, counting attempted records even on loss.
 * @param kind One for received packet, two for status.
 * @param stamp Extended radio timestamp in microseconds.
 * @param body Borrowed payload bytes.
 * @param length Payload byte count, at most SNIFFER_MAX_BODY. */
static void emit(unsigned kind, uint64_t stamp, const void *body, size_t length)
{
	uint8_t wire[SNIFFER_MAX_RECORD];
	size_t size = sniff_record(wire, kind, ++record_sequence, stamp, body,
				   length);

	if (size)
		uart0_write(wire, size);
}

/** @brief Emit identity and cumulative loss counters once per second. */
static void status(void)
{
	static const char identity[] = EC_BOARD_MARKER;
	uint8_t body[96] = { 0 };
	uint32_t values[] = { EC_UART_BAUD,   rf_drops,	      uart_lost,
			      bad_packets,    time_errors,    cal_errors,
			      uart_rx_errors, uart_dma_errors };

	sniff_put(body, EC_VERSION_MAJOR, 2);
	sniff_put(body + 2, EC_VERSION_MINOR, 2);
	for (unsigned i = 0; i < sizeof(values) / sizeof(values[0]); i++)
		sniff_put(body + 4 + i * 4, values[i], 4);
	memcpy(body + 36, identity, sizeof(identity));
	emit(2, radio_time(), body, 36 + sizeof(identity));
}

/** @brief Accept only an exact maintenance line; no RF commands exist.
 * @param byte UART input byte, or zero after a transport resynchronization.
 * @return Always zero. */
static int maintenance(unsigned char byte)
{
	static char line[32];
	static unsigned used;
	static int invalid;

	if (byte == '\n') {
		if (!invalid && used == 10 && !memcmp(line, "BOOTLOADER", 10)) {
			RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
			ec_mg21_bootloader();
		}
		used = 0;
		invalid = 0;
	} else if (byte < 32 || byte > 126 || used == sizeof(line)) {
		invalid = 1;
	} else if (!invalid) {
		line[used++] = byte;
	}
	return 0;
}

/** @brief Retain complete packets and count RF FIFO loss without UART ISR work.
 * @param handle RAIL instance raising the event.
 * @param events Radio event bitmask. */
static void radio_event(RAIL_Handle_t handle, RAIL_Events_t events)
{
	if (events & RAIL_EVENT_RX_PACKET_RECEIVED)
		RAIL_HoldRxPacket(handle);
	if (events & RAIL_EVENT_RX_FIFO_OVERFLOW)
		rf_drops++;
	if (events & RAIL_EVENT_CAL_NEEDED)
		cal_needed = 1;
}

/** @brief Fail closed on SDK errors before entering the capture loop.
 * @param result SDK result, zero for success. */
static void require_ok(RAIL_Status_t result)
{
	if (result == RAIL_STATUS_NO_ERROR)
		return;
	for (;;)
		__WFI();
}

/** @brief Start passive channel-11 802.15.4 reception; never configure TX. */
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
	RAIL_StateTransitions_t transitions = { RAIL_RF_STATE_RX,
						RAIL_RF_STATE_RX };
	uint16_t size = sizeof(rx_fifo);

	radio = RAIL_Init(&cfg, NULL);
	require_ok(radio ? RAIL_STATUS_NO_ERROR : RAIL_STATUS_INVALID_STATE);
	require_ok(RAIL_IEEE802154_Config2p4GHzRadio(radio));
	require_ok(RAIL_IEEE802154_Init(radio, &ieee));
	require_ok(RAIL_ConfigRxOptions(radio, RAIL_RX_OPTION_STORE_CRC, 0));
	require_ok(RAIL_SetRxFifo(radio, rx_fifo, &size));
	require_ok(RAIL_SetRxTransitions(radio, &transitions));
	require_ok(RAIL_ConfigCal(radio, RAIL_CAL_ALL));
	require_ok(RAIL_ConfigEvents(radio, RAIL_EVENTS_ALL,
				     RAIL_EVENT_RX_PACKET_RECEIVED |
					     RAIL_EVENT_RX_FIFO_OVERFLOW |
					     RAIL_EVENT_CAL_NEEDED));
	require_ok(RAIL_StartRx(radio, 11, NULL));
}

/** @brief Copy a held frame and hardware timestamp before releasing its buffer. */
static void radio_poll(void)
{
	RAIL_RxPacketInfo_t info;
	RAIL_RxPacketDetails_t details;
	RAIL_RxPacketHandle_t packet;
	uint8_t bytes[128], body[129];
	uint64_t now;
	uint32_t age;

	packet = RAIL_GetRxPacketInfo(
		radio, RAIL_RX_PACKET_HANDLE_OLDEST_COMPLETE, &info);
	if (packet != RAIL_RX_PACKET_HANDLE_INVALID) {
		if (info.packetStatus != RAIL_RX_PACKET_READY_SUCCESS ||
		    info.packetBytes < 2 || info.packetBytes > 126) {
			bad_packets++;
		} else if (RAIL_GetRxPacketDetailsAlt(radio, packet,
						      &details) !=
				   RAIL_STATUS_NO_ERROR ||
			   RAIL_GetRxTimeSyncWordEnd(
				   radio, info.packetBytes + 2,
				   &details.timeReceived.packetTime) !=
				   RAIL_STATUS_NO_ERROR) {
			time_errors++;
		} else {
			RAIL_CopyRxPacket(bytes, &info);
			now = radio_time();
			age = (uint32_t)now - details.timeReceived.packetTime;
			if (age > 1000000 || age > now) {
				time_errors++;
			} else if ((bytes[0] & 0x80) ||
				   bytes[0] != info.packetBytes + 1) {
				bad_packets++;
			} else {
				body[0] = (uint8_t)(int8_t)details.rssi;
				body[1] = details.lqi;
				body[2] = 11;
				body[3] =
					0; /* FCS validated by radio and omitted. */
				memcpy(body + 4, bytes + 1,
				       info.packetBytes - 1);
				emit(1, now - age, body, info.packetBytes + 3);
			}
		}
		RAIL_ReleaseRxPacket(radio, packet);
	}
	if (cal_needed) {
		cal_needed = 0;
		if (RAIL_Calibrate(radio, NULL, RAIL_CAL_ALL_PENDING) !=
		    RAIL_STATUS_NO_ERROR)
			cal_errors++;
	}
}

/** @brief Initialize the Sonoff board and run only passive capture/maintenance.
 * @return Never returns. */
int main(void)
{
	CMU_HFXOInit_TypeDef hfxo = CMU_HFXOINIT_DEFAULT;
	uint32_t last_status = 0;

	CHIP_Init();
	hfxo.ctuneXiAna = EC_BOARD_HFXO_CTUNE;
	hfxo.ctuneXoAna = EC_BOARD_HFXO_CTUNE;
	CMU_HFXOInit(&hfxo);
	CMU_ClockSelectSet(cmuClock_SYSCLK, cmuSelect_HFXO);
	SystemCoreClockUpdate();
	SysTick_Config(SystemCoreClock / CLOCK_SECOND);
	uart_init();
	radio_init();
	uart_rx_init();
	uart0_set_callback(maintenance);
	status();
	for (;;) {
		radio_time();
		radio_poll();
		uart_rx_poll();
		if ((uint32_t)(clock_time() - last_status) >= 1000) {
			last_status = clock_time();
			status();
		}
	}
}
