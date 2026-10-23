/*
 * Bring-up diagnostics independent of the TI UART driver and scheduler.
 * No globals are used before the C runtime has initialized RAM.
 */
#include <stdint.h>
#include <ti/devices/DeviceFamily.h>
/* SDK include paths are stringified by the macro. */
/* clang-format off */
#include DeviceFamily_constructPath(driverlib/prcm.h)
#include DeviceFamily_constructPath(driverlib/ioc.h)
#include DeviceFamily_constructPath(driverlib/uart.h)
#include DeviceFamily_constructPath(driverlib/aon_ioc.h)
/* clang-format on */
#include "dev/uart0-arch.h"
#include <ti/drivers/PIN.h>
#include <ti/drivers/Power.h>

static int normal_uart_ready;

/**
 * @brief Make boot diagnostics available before the normal UART driver is ready.
 */
static void early_uart_init(void)
{
	PRCMPowerDomainOn(PRCM_DOMAIN_SERIAL);
	while (PRCMPowerDomainStatus(PRCM_DOMAIN_SERIAL) !=
	       PRCM_DOMAIN_POWER_ON) {
	}
	PRCMPeripheralRunEnable(PRCM_PERIPH_UART0);
	PRCMLoadSet();
	while (!PRCMLoadGet()) {
	}
	IOCPortConfigureSet(IOID_13, IOC_PORT_MCU_UART0_TX, IOC_STD_OUTPUT);
	AONIOCFreezeDisable();
	UARTConfigSetExpClk(UART0_BASE, 48000000, 115200,
			    UART_CONFIG_WLEN_8 | UART_CONFIG_STOP_ONE |
				    UART_CONFIG_PAR_NONE);
	UARTEnable(UART0_BASE);
}

/**
 * @brief Deliver a complete diagnostic message before boot proceeds.
 *
 * @param text NUL-terminated diagnostic message.
 */
static void early_puts(const char *text)
{
	while (*text)
		UARTCharPut(UART0_BASE, *text++);
	while (UARTBusy(UART0_BASE)) {
	}
}

/**
 * @brief Initialize the SDK power subsystem through the linker-provided original.
 *
 * @return SDK power initialization status.
 */
int_fast16_t __real_Power_init(void);

/**
 * @brief Expose power initialization progress through the boot diagnostic UART.
 *
 * @return Status returned by the SDK power initializer.
 */
int_fast16_t __wrap_Power_init(void)
{
	int_fast16_t status;

	if (normal_uart_ready)
		return __real_Power_init();
	early_uart_init();
	early_puts("BOOT power init\n");
	status = __real_Power_init();
	early_uart_init();
	early_puts("BOOT power ready\n");
	return status;
}

/**
 * @brief Initialize SDK pin configuration through the linker-provided original.
 *
 * @param config Pin configuration table expected by the SDK.
 *
 * @return SDK pin initialization status.
 */
PIN_Status __real_PIN_init(const PIN_Config *config);

/**
 * @brief Expose pin initialization progress and restore the diagnostic UART.
 *
 * @param config Pin configuration table expected by the SDK.
 *
 * @return Status returned by the SDK pin initializer.
 */
PIN_Status __wrap_PIN_init(const PIN_Config *config)
{
	PIN_Status status;

	early_puts("BOOT pins init\n");
	status = __real_PIN_init(config);
	early_uart_init();
	early_puts(status == PIN_SUCCESS ? "BOOT pins ready\n"
					 : "BOOT pins FAILED\n");
	return status;
}

/**
 * @brief Start the original SDK interrupt environment.
 */
void __real_NoRTOS_start(void);

/**
 * @brief Expose interrupt startup progress through the diagnostic UART.
 */
void __wrap_NoRTOS_start(void)
{
	early_uart_init();
	early_puts("BOOT interrupts enable\n");
	__real_NoRTOS_start();
	early_puts("BOOT interrupts ready\n");
}

/**
 * @brief Apply the original SDK device trim configuration.
 */
void __real_NOROM_SetupTrimDevice(void);

/**
 * @brief Expose device trimming progress through the diagnostic UART.
 */
void __wrap_NOROM_SetupTrimDevice(void)
{
	early_uart_init();
	early_puts("BOOT before trim\n");
	__real_NOROM_SetupTrimDevice();
	early_uart_init();
	early_puts("BOOT after trim\n");
}

/**
 * @brief Run the original first platform initialization stage.
 */
void __real_platform_init_stage_one(void);

/**
 * @brief Expose first-stage platform startup through the diagnostic UART.
 */
void __wrap_platform_init_stage_one(void)
{
	early_puts("BOOT platform one\n");
	__real_platform_init_stage_one();
	early_uart_init();
	early_puts("BOOT platform one complete\n");
}

/**
 * @brief Run the original second platform initialization stage.
 */
void __real_platform_init_stage_two(void);

/**
 * @brief Announce normal UART readiness after second-stage platform startup.
 */
void __wrap_platform_init_stage_two(void)
{
	static const char ready[] = "BOOT UART ready\n";

	early_puts("BOOT platform two\n");
	__real_platform_init_stage_two();
	uart0_write(ready, sizeof(ready) - 1);
	normal_uart_ready = 1;
}
