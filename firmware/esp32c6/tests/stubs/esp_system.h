/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ESP_SYSTEM_H
#define TEST_ESP_SYSTEM_H

typedef int esp_reset_reason_t;

/** @brief Supply the fixed synthetic boot reason for health diagnostics.
 * @return Mock ESP-IDF reset reason, with no reset or hardware access.
 */
esp_reset_reason_t esp_reset_reason(void);

#endif
