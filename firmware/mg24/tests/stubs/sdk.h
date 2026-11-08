/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_MG24_TEST_SDK_H
#define EC_MG24_TEST_SDK_H
#include <stdint.h>
#include <stdbool.h>

typedef struct {
	uint32_t STATUS, RXDATA, TXDATA;
} USART_TypeDef;
typedef struct {
	struct {
		uint32_t TXROUTE, RXROUTE, ROUTEEN;
	} USARTROUTE[1];
} GPIO_TypeDef;
typedef struct {
	unsigned baudrate;
} USART_InitAsync_TypeDef;
typedef struct {
	unsigned ctuneXiAna, ctuneXoAna;
} CMU_HFXOInit_TypeDef;
typedef struct {
	unsigned magic, structVersion, signatureType;
	struct {
		unsigned type, version;
		char productId[16];
	} app;
} ApplicationProperties_t;
extern GPIO_TypeDef fixture_gpio;
extern unsigned fixture_irq_disabled;
extern uint32_t fixture_flags;

/** @brief Expose the simulated USART, committing any prior register write.
 * @return Stable fake peripheral storage.
 */
USART_TypeDef *fixture_uart(void);
/** @brief Enable simulated interrupt sources, including an immediate pending IRQ.
 * @param usart Fake USART instance.
 * @param flags Interrupt bits to enable.
 */
void fixture_enable(USART_TypeDef *usart, uint32_t flags);
/** @brief Record exactly which peripheral interrupt flags main or RX clears.
 * @param usart Fake USART instance.
 * @param flags Pending flags selected for clearing.
 */
void fixture_clear(USART_TypeDef *usart, uint32_t flags);
/** @brief Disable simulated interrupt sources without discarding copied bytes.
 * @param usart Fake USART instance.
 * @param flags Interrupt bits to disable.
 */
void fixture_disable(USART_TypeDef *usart, uint32_t flags);
/** @brief Verify the application leaves UART output at lowest interrupt priority.
 * @param irq Selected IRQ number.
 * @param priority Unshifted CMSIS priority.
 */
void fixture_priority(unsigned irq, unsigned priority);
/** @brief Confirm the transport baud and asynchronous initialization.
 * @param usart Fake USART instance.
 * @param config Requested UART configuration.
 */
void fixture_init(USART_TypeDef *usart, const USART_InitAsync_TypeDef *config);

#define USART0 fixture_uart()
#define GPIO (&fixture_gpio)
#define USART_STATUS_TXBL (1u << 6)
#define USART_STATUS_RXDATAV (1u << 7)
#define USART_IF_TXBL (1u << 1)
#define USART_IF_RXDATAV (1u << 2)
#define USART_IF_RXOF (1u << 4)
#define USART_IF_FERR (1u << 9)
#define USART_IF_PERR (1u << 8)
#define USART_IEN_RXDATAV USART_IF_RXDATAV
#define USART_IEN_RXOF USART_IF_RXOF
#define USART_IEN_FERR USART_IF_FERR
#define USART_IEN_PERR USART_IF_PERR
#define USART_IEN_TXBL (1u << 1)
#define _USART_IF_MASK 0xffffffffu
#define USART_INITASYNC_DEFAULT { 0 }
#define CMU_HFXOINIT_DEFAULT { 0, 0 }
#define _GPIO_USART_TXROUTE_PIN_SHIFT 0
#define _GPIO_USART_RXROUTE_PIN_SHIFT 0
#define GPIO_USART_ROUTEEN_TXPEN 1
#define GPIO_USART_ROUTEEN_RXPEN 2
#define USART0_RX_IRQn 11u
#define USART0_TX_IRQn 10u
#define __NVIC_PRIO_BITS 4
#define CHIP_Init() ((void)0)
#define SystemCoreClockUpdate() ((void)0)
#define CMU_ClockEnable(...) ((void)0)
#define CMU_ClockSelectSet(...) ((void)0)
#define CMU_HFXOInit(config) ((void)(config))
#define GPIO_PinModeSet(...) ((void)0)
#define USART_InitAsync fixture_init
#define USART_IntGet(usart) ((void)(usart), fixture_flags)
#define USART_IntClear fixture_clear
#define USART_IntEnable fixture_enable
#define USART_IntDisable fixture_disable
#define NVIC_SetPriority fixture_priority
#define NVIC_EnableIRQ(irq) ((void)(irq))
#define __disable_irq() (fixture_irq_disabled = 1)
#define __enable_irq() (fixture_irq_disabled = 0)
#define APPLICATION_PROPERTIES_MAGIC 1
#define APPLICATION_PROPERTIES_VERSION 1
#define APPLICATION_SIGNATURE_NONE 0
#define APPLICATION_TYPE_FLEX 1
#endif
