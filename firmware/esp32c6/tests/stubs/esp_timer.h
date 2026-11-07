/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ESP_TIMER_H
#define TEST_ESP_TIMER_H

#include <stdint.h>

/** @brief Read the synthetic boot-relative timer used by health diagnostics.
 * @return Mock elapsed microseconds.
 */
int64_t esp_timer_get_time(void);

#endif
