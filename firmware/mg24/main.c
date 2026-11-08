/* SPDX-License-Identifier: BSD-2-Clause */
#include "em_chip.h"
#include "em_cmu.h"
#include "em_gpio.h"
#include "em_usart.h"
#include "api/application_properties.h"
#include "transport.h"
#include "../version.h"
#ifdef EC_RF_DIAGNOSTICS
#include "rf.h"
extern const struct ec_rf_ops ec_mg24_rf_ops;
#ifdef EC_RF_REJECT_DIAGNOSTICS
#include "reject.h"
#endif
#endif

#define RX_SIZE 2048u
static volatile uint8_t rx[RX_SIZE];
static volatile unsigned head, tail, lost;

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
	uint32_t flags = USART_IntGet(USART0);

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
}

/** @brief Keep RF off until a bounded RFSTART or receive-only RFLISTEN request.
 * @return Never returns.
 */
int main(void)
{
	struct ec_experimental parser;
	char response[640];
	size_t pending = 0, sent = 0;

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
	parser.build = "xiao-queue-1";
#ifdef EC_RF_REJECT_DIAGNOSTICS
	parser.radio_command = ec_mg24_reject_command;
	parser.build = "xiao-reject-1";
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
		if (sent < pending) {
			if (USART0->STATUS & USART_STATUS_TXBL)
				USART0->TXDATA = response[sent++];
			continue;
		}
		pending = sent = 0;
		if (tail != head) {
			uint8_t byte = rx[tail];

			tail = (tail + 1) & (RX_SIZE - 1);
			pending = ec_experimental_byte(&parser, byte, response,
						       sizeof(response));
			sent = 0;
		}
#ifdef EC_RF_DIAGNOSTICS
		if (!pending) {
			pending = ec_rf_event(response, sizeof(response));
			sent = 0;
		}
#endif
	}
}
