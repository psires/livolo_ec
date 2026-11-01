/* USB and the gateway timer share a continuously running crystal clock.
 * The radio driver may release its reference but must not stop that clock.
 * POWER_CLOCK IRQ belongs to nrfx_power for USB power events.
 */
#include "nrf.h"
#include "nrf_clock.h"
#include "platform/clock/nrf_802154_clock.h"
#include "ec-nrf-platform.h"

static volatile unsigned hf_pending;

/** @brief Clocks are initialized by main before the driver starts. */
void nrf_802154_clock_init(void)
{
}
/** @brief Keep the USB and scheduler clocks running on driver shutdown. */
void nrf_802154_clock_deinit(void)
{
}
/** @brief Schedule the driver's asynchronous HF clock-ready notification. */
void nrf_802154_clock_hfclk_start(void)
{
	hf_pending = 1;
}
/** @brief Release the radio's logical reference; USB still needs HFXO. */
void nrf_802154_clock_hfclk_stop(void)
{
	hf_pending = 0;
}
/** @brief Query crystal readiness. @return True when HFXO runs. */
bool nrf_802154_clock_hfclk_is_running(void)
{
	return nrf_clock_hf_is_running(NRF_CLOCK_HFCLK_HIGH_ACCURACY);
}
/** @brief Notify the driver synchronously: its initializer waits for this. */
void nrf_802154_clock_lfclk_start(void)
{
	nrf_802154_clock_lfclk_ready();
}
/** @brief Keep the low-frequency source available for the driver RTC. */
void nrf_802154_clock_lfclk_stop(void)
{
	/* LFCLK remains available. */
}
/** @brief Query LF clock readiness. @return True when LFCLK runs. */
bool nrf_802154_clock_lfclk_is_running(void)
{
	return nrf_clock_lf_is_running();
}
/** @brief Deliver deferred clock callbacks outside driver request stacks. */
void ec_nrf_clock_poll(void)
{
	uintptr_t key = HwiP_disable();
	unsigned hf = hf_pending;
	hf_pending = 0;
	HwiP_restore(key);
	if (hf)
		nrf_802154_clock_hfclk_ready();
}
