/* SPDX-License-Identifier: BSD-2-Clause */
#include "em_chip.h"
#include "em_cmu.h"
#include "em_gpio.h"
#include "em_usart.h"
#include "api/application_properties.h"
#include "transport.h"
#include "../version.h"
#include <stdatomic.h>
#ifdef EC_RF_DIAGNOSTICS
#include "rf.h"
extern const struct ec_rf_ops ec_mg24_rf_ops;
#ifdef EC_RF_REJECT_DIAGNOSTICS
#include "reject.h"
#endif
#endif

#define RX_SIZE 2048u
#define TX_IRQ_BUDGET 8u
#define INPUT_BUDGET 64u
_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
	       "UART ISR requires lock-free ownership");
static volatile uint8_t rx[RX_SIZE];
static volatile unsigned head, tail, lost;
static const char *tx_buffer;
static size_t tx_sent;
static atomic_uint tx_pending;

/** @brief Feed only ready UART slots without delaying higher-priority radio IRQs. */
void USART0_TX_IRQHandler(void)
{
	unsigned length =
		atomic_load_explicit(&tx_pending, memory_order_acquire);

	if (!length) {
		USART_IntDisable(USART0, USART_IEN_TXBL);
		return;
	}
	for (unsigned budget = 0; budget < TX_IRQ_BUDGET && tx_sent < length;
	     budget++) {
		if (!(USART0->STATUS & USART_STATUS_TXBL))
			break;
		USART0->TXDATA = (uint8_t)tx_buffer[tx_sent++];
	}
	if (tx_sent == length) {
		USART_IntDisable(USART0, USART_IEN_TXBL);
		/* The UART now owns copied bytes; physical wire completion is later. */
		atomic_store_explicit(&tx_pending, 0, memory_order_release);
	}
}

/** @brief Give one complete output record to the UART while preserving its bytes.
 * @param data Immutable record storage, retained until tx_pending becomes zero.
 * @param length Record bytes, at most the main response buffer capacity.
 * @note Main is the sole publisher and must acquire zero tx_pending first.
 * Publication may race a stale TX IRQ: the complete record is visible before
 * arming, and an empty IRQ always disarms itself. No IRQ waits for UART space.
 */
static void serial_output_start(const char *data, size_t length)
{
	if (!length)
		return;
	tx_buffer = data;
	tx_sent = 0;
	atomic_store_explicit(&tx_pending, (unsigned)length,
			      memory_order_release);
	USART_IntEnable(USART0, USART_IEN_TXBL);
}

/* The preserved Gecko loader finds this through vector slot 13. */
const ApplicationProperties_t sl_app_properties = {
	.magic = APPLICATION_PROPERTIES_MAGIC,
	.structVersion = APPLICATION_PROPERTIES_VERSION,
	.signatureType = APPLICATION_SIGNATURE_NONE,
	.app = {
		.type = APPLICATION_TYPE_FLEX,
		.version = (EC_VERSION_MAJOR << 16) | EC_VERSION_MINOR,
		.productId = { 'L', 'I', 'V', 'O', 'L', 'O', '-', 'X', 'I',
			       'A', 'O', '-', 'M', 'G', '2', '4' },
	},
};

/** @brief Drain USART0 into a bounded RX ring; never execute commands in IRQ. */
void USART0_RX_IRQHandler(void)
{
	uint32_t flags =
		USART_IntGet(USART0) & (USART_IF_RXDATAV | USART_IF_RXOF |
					USART_IF_FERR | USART_IF_PERR);

	if (flags & (USART_IF_RXOF | USART_IF_FERR | USART_IF_PERR))
		lost = 1;
	USART_IntClear(USART0, flags);
	while (USART0->STATUS & USART_STATUS_RXDATAV) {
		uint8_t byte = USART0->RXDATA;
		unsigned next = (head + 1) & (RX_SIZE - 1);

		if (next == tail) {
			lost = 1;
		} else {
			rx[head] = byte;
			head = next;
		}
	}
}

/** @brief Initialize only the XIAO bridge's PA8/PA9 USART at 115200 baud. */
static void serial_init(void)
{
	USART_InitAsync_TypeDef config = USART_INITASYNC_DEFAULT;

	CMU_ClockEnable(cmuClock_GPIO, true);
	CMU_ClockEnable(cmuClock_USART0, true);
	GPIO_PinModeSet(gpioPortA, 8, gpioModePushPull, 1);
	GPIO_PinModeSet(gpioPortA, 9, gpioModeInputPull, 1);
	config.baudrate = 115200;
	USART_InitAsync(USART0, &config);
	GPIO->USARTROUTE[0].TXROUTE = 8 << _GPIO_USART_TXROUTE_PIN_SHIFT;
	GPIO->USARTROUTE[0].RXROUTE = 9 << _GPIO_USART_RXROUTE_PIN_SHIFT;
	GPIO->USARTROUTE[0].ROUTEEN =
		GPIO_USART_ROUTEEN_TXPEN | GPIO_USART_ROUTEEN_RXPEN;
	USART_IntClear(USART0, _USART_IF_MASK);
	USART_IntEnable(USART0, USART_IEN_RXDATAV | USART_IEN_RXOF |
					USART_IEN_FERR | USART_IEN_PERR);
	NVIC_EnableIRQ(USART0_RX_IRQn);
	/* Lowest priority prevents output service from preempting radio IRQs. */
	NVIC_SetPriority(USART0_TX_IRQn, (1u << __NVIC_PRIO_BITS) - 1);
	NVIC_EnableIRQ(USART0_TX_IRQn);
}

/** @brief Keep RF off until a bounded RFSTART or receive-only RFLISTEN request.
 * @return Never returns.
 */
int main(void)
{
	struct ec_experimental parser;
	char response[640];
	size_t pending;
#ifdef EC_RF_DIAGNOSTICS
	unsigned prefer_event = 0;
#endif

	CHIP_Init();
#ifdef EC_RF_DIAGNOSTICS
	/* Board DTS uses a 39 MHz crystal with CTUNE 95; no radio starts here. */
	CMU_HFXOInit_TypeDef hfxo = CMU_HFXOINIT_DEFAULT;

	hfxo.ctuneXiAna = hfxo.ctuneXoAna = 95;
	CMU_HFXOInit(&hfxo);
	CMU_ClockSelectSet(cmuClock_SYSCLK, cmuSelect_HFXO);
#endif
	SystemCoreClockUpdate();
	serial_init();
	ec_experimental_init(&parser, "seeed-xiao-mg24", "xiao-bringup-1",
			     115200);
#ifdef EC_RF_DIAGNOSTICS
	ec_rf_setup(&ec_mg24_rf_ops);
	parser.radio_command = ec_rf_command;
	parser.build = "xiao-uart-1";
	parser.adapter_diagnostics = 1;
#ifdef EC_RF_REJECT_DIAGNOSTICS
	parser.radio_command = ec_mg24_reject_command;
	parser.build = "xiao-reject-1";
	parser.adapter_diagnostics = 0;
#endif
#endif
	for (;;) {
#ifdef EC_RF_DIAGNOSTICS
		ec_rf_poll();
#endif
		if (lost) {
			__disable_irq();
			tail = head;
			lost = 0;
			__enable_irq();
			ec_experimental_byte(&parser, 0, response,
					     sizeof(response));
		}
		pending =
			atomic_load_explicit(&tx_pending, memory_order_acquire);
#ifdef EC_RF_DIAGNOSTICS
		if (!pending && prefer_event) {
			pending = ec_rf_event(response, sizeof(response));
			prefer_event = 0;
			serial_output_start(response, pending);
			if (pending)
				continue;
		}
#endif
		for (unsigned budget = 0; budget < INPUT_BUDGET && tail != head;
		     budget++) {
			uint8_t byte = rx[tail];
			size_t length;

			/* Only newline can produce output; retain the owned record. */
			if (byte == '\n' && pending)
				break;
			tail = (tail + 1) & (RX_SIZE - 1);
			length = ec_experimental_byte(&parser, byte, response,
						      sizeof(response));
			if (length) {
				pending = length;
				serial_output_start(response, length);
#ifdef EC_RF_DIAGNOSTICS
				prefer_event = 1;
#endif
				break;
			}
		}
#ifdef EC_RF_DIAGNOSTICS
		if (!pending) {
			pending = ec_rf_event(response, sizeof(response));
			prefer_event = 0;
			serial_output_start(response, pending);
		}
#endif
	}
}
