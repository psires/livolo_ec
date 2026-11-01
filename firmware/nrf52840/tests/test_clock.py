"""Protect the Nordic driver's synchronous LF-clock startup requirement."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ClockContract(unittest.TestCase):
    def test_clock_ready_before_initializer_can_wait(self):
        """LF readiness must be delivered before a blocking driver init resumes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "platform/clock").mkdir(parents=True)
            (root / "nrf.h").write_text("")
            (root / "nrf_clock.h").write_text(
                """#include <stdbool.h>
#define NRF_CLOCK_HFCLK_HIGH_ACCURACY 1
/** @brief Simulate readiness of the requested high-frequency clock.
 * @param x Clock selector; 1 represents high accuracy in this fixture.
 * @return True for the supported selector, otherwise false.
 */
static inline bool nrf_clock_hf_is_running(int x)
{
	return x == 1;
}
/** @brief Simulate an already running low-frequency clock.
 * @return True unconditionally.
 */
static inline bool nrf_clock_lf_is_running(void)
{
	return true;
}
"""
            )
            (root / "platform/clock/nrf_802154_clock.h").write_text(
                "void nrf_802154_clock_hfclk_ready(void);\n"
                "void nrf_802154_clock_lfclk_ready(void);\n"
            )
            (root / "stub.h").write_text(
                """#include <stdint.h>
#define EC_NRF_PLATFORM_H
/** @brief Provide a no-op interrupt guard for the single-threaded test.
 * @return Synthetic interrupt state key, always zero.
 */
static inline uintptr_t HwiP_disable(void)
{
	return 0;
}
/** @brief Accept restoration of a synthetic interrupt state.
 * @param key Unused state key returned by the fixture guard.
 */
static inline void HwiP_restore(uintptr_t key)
{
	(void)key;
}
"""
            )
            (root / "main.c").write_text(
                """#include <assert.h>
#include "ec-clock.c"
static unsigned hf, lf;
/** @brief Count high-frequency readiness callbacks. */
void nrf_802154_clock_hfclk_ready(void)
{
	hf++;
}
/** @brief Count low-frequency readiness callbacks. */
void nrf_802154_clock_lfclk_ready(void)
{
	lf++;
}
/** @brief Check synchronous LF and deferred cancellable HF readiness.
 * @return Zero after all assertions pass; abort on a contract violation.
 */
int main(void)
{
	nrf_802154_clock_init();
	nrf_802154_clock_lfclk_start();
	assert(lf == 1); /* Driver waits right here; no main loop yet. */
	nrf_802154_clock_hfclk_start();
	assert(hf == 0);
	ec_nrf_clock_poll();
	assert(hf == 1 && lf == 1);
	ec_nrf_clock_poll();
	assert(hf == 1 && lf == 1);
	nrf_802154_clock_hfclk_start();
	nrf_802154_clock_hfclk_stop();
	ec_nrf_clock_poll();
	assert(hf == 1);
	nrf_802154_clock_lfclk_stop();
	nrf_802154_clock_lfclk_start();
	assert(lf == 2);
	assert(nrf_802154_clock_hfclk_is_running());
	assert(nrf_802154_clock_lfclk_is_running());
	return 0;
}
"""
            )
            binary = root / "test"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(root),
                    "-I",
                    str(ROOT),
                    "-include",
                    str(root / "stub.h"),
                    str(root / "main.c"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
