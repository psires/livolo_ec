#include "ec-update.h"
#include <ti/devices/DeviceFamily.h>
/* SDK include paths are stringified by the macro. */
/* clang-format off */
#include DeviceFamily_constructPath(driverlib/flash.h)
#include DeviceFamily_constructPath(driverlib/interrupt.h)
#include DeviceFamily_constructPath(driverlib/watchdog.h)
#include DeviceFamily_constructPath(driverlib/vims.h)
#include DeviceFamily_constructPath(driverlib/prcm.h)
#include DeviceFamily_constructPath(driverlib/sys_ctrl.h)
/* clang-format on */
#include <string.h>
#include "update-public-key.h"
const uint8_t ec_update_public_key[32] = EC_UPDATE_PUBLIC_KEY;

/**
 * @brief Expose a bounded, read-only view of device flash.
 *
 * @param a First flash byte address.
 * @param n Number of readable bytes requested.
 *
 * @return Pointer to the requested range, or NULL when outside flash.
 */
const uint8_t *ec_flash_read(uint32_t a, size_t n)
{
	return a <= EC_FLASH_SIZE && n <= EC_FLASH_SIZE - a
		       ? (const uint8_t *)(uintptr_t)a
		       : 0;
}

/**
 * @brief Check whether an aligned write stays outside protected flash.
 *
 * @param a Destination flash address.
 * @param n Requested byte count.
 *
 * @return Nonzero for an allowed range, otherwise zero.
 */
static int valid_range(uint32_t a, size_t n)
{
	return a >= EC_APP && a < EC_FLASH_SIZE - EC_SECTOR &&
	       n <= EC_FLASH_SIZE - EC_SECTOR - a && !((a | n) & 3);
}
/*
 * Flash writes do not invalidate VIMS or its line buffers. In particular,
 * journal readback must not see a cached state from before programming.
 */
/**
 * @brief Make flash mutations and their readback independent of stale cache data.
 *
 * @return Previous VIMS mode to pass to flash_cache_restore().
 */
static uint32_t flash_cache_disable(void)
{
	uint32_t mode;

	do {
		mode = VIMSModeGet(VIMS_BASE);
	} while (mode == VIMS_MODE_CHANGING);
	VIMSLineBufDisable(VIMS_BASE);
	VIMSModeSafeSet(VIMS_BASE, VIMS_MODE_DISABLED, true);
	return mode;
}

/**
 * @brief Restore normal flash access after a flash mutation.
 *
 * @param mode VIMS mode saved by flash_cache_disable().
 */
static void flash_cache_restore(uint32_t mode)
{
	VIMSModeSafeSet(VIMS_BASE, mode, true);
	VIMSLineBufEnable(VIMS_BASE);
}

/**
 * @brief Erase an allowed flash sector safely.
 *
 * @param a Sector-aligned destination address.
 *
 * @return Zero on success, or -1 for an invalid request or hardware failure.
 */
int ec_flash_erase(uint32_t a)
{
	bool disabled;
	uint32_t mode, result;

	if (!valid_range(a, EC_SECTOR) || a % EC_SECTOR ||
	    FlashSectorSizeGet() != EC_SECTOR)
		return -1;
	disabled = IntMasterDisable();
	mode = flash_cache_disable();
	result = FlashSectorErase(a);
	flash_cache_restore(mode);
	if (!disabled)
		IntMasterEnable();
	return result == FAPI_STATUS_SUCCESS ? 0 : -1;
}

/**
 * @brief Program aligned flash data from SRAM while preserving platform state.
 *
 * @param a Word-aligned destination address.
 * @param p Source bytes held in SRAM.
 * @param n Positive byte count divisible by four.
 *
 * @return Zero on success, or -1 for an invalid request or hardware failure.
 */
int ec_flash_program(uint32_t a, const uint8_t *p, size_t n)
{
	bool disabled;
	uint32_t mode, result;

	if (!valid_range(a, n) || !n || (uintptr_t)p < 0x20000000 ||
	    (uintptr_t)p + n > 0x20014000)
		return -1;
	disabled = IntMasterDisable();
	mode = flash_cache_disable();
	result = FlashProgram((uint8_t *)p, a, n);
	flash_cache_restore(mode);
	if (!disabled)
		IntMasterEnable();
	return result == FAPI_STATUS_SUCCESS ? 0 : -1;
}

/**
 * @brief Keep an enabled watchdog alive during lengthy update work.
 */
void ec_update_watchdog_feed(void)
{
	if (WatchdogRunning()) {
		WatchdogUnlock();
		WatchdogIntClear();
		WatchdogReloadSet(45000000);
		WatchdogLock();
	}
}

/**
 * @brief Enable watchdog recovery for boot and update operations.
 */
void ec_update_watchdog_start(void)
{
	WatchdogUnlock();
	WatchdogReloadSet(45000000);
	WatchdogResetEnable();
	WatchdogEnable();
	WatchdogLock();
}

/**
 * @brief Restart the device for firmware activation or recovery.
 */
void ec_update_reset(void)
{
	SysCtrlSystemReset();
	for (;;) {
	}
}
