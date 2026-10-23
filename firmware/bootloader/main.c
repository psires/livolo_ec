#include "../update/ec-update.h"
#include "../version.h"
#include <ti/devices/DeviceFamily.h>
/* SDK include paths are stringified by the macro. */
/* clang-format off */
#include DeviceFamily_constructPath(driverlib/setup.h)
/* clang-format on */
/**
 * @brief Enable watchdog recovery for boot and update operations.
 */
extern void ec_update_watchdog_start(void);
extern uint32_t __data_load__, __data_start__, __data_end__, __bss_start__,
	__bss_end__;

/**
 * @brief Initialize the boot environment and run only an authenticated application.
 */
void boot_main(void)
{
	uint32_t *src = &__data_load__;

	for (uint32_t *dst = &__data_start__; dst < &__data_end__;)
		*dst++ = *src++;
	for (uint32_t *dst = &__bss_start__; dst < &__bss_end__;)
		*dst++ = 0;
	SetupTrimDevice();
	ec_update_watchdog_start();
	if (ec_update_boot()) {
		uint32_t sp, pc;

		*(volatile uint32_t *)0xe000ed08 = EC_APP; /* VTOR */
		__asm__ volatile("dsb\n isb\n");
		sp = *(const uint32_t *)EC_APP;
		pc = *(const uint32_t *)(EC_APP + 4);
		__asm__ volatile("msr msp,%0\n bx %1" ::"r"(sp), "r"(pc)
				 : "memory");
	}
	/* No unverified branch. Hardware watchdog retries; ROM recovery remains. */
	for (;;) {
	}
}

/**
 * @brief Enter boot initialization with interrupts disabled after reset.
 */
__attribute__((naked)) void resetISR(void)
{
	__asm__ volatile("cpsid i\n b boot_main");
}
__attribute__((section(".vectors"), used))
const uintptr_t vectors[2] = { 0x20014000, (uintptr_t)resetISR };
__attribute__((section(".boot_version"), used)) const char boot_version[] =
	"EC-BOOT " EC_BOOT_VERSION;
