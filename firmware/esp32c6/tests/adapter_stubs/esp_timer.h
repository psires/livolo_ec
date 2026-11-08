/* SPDX-License-Identifier: BSD-2-Clause */
#define esp_timer_get_time() (test_clock_calls++, 123)
