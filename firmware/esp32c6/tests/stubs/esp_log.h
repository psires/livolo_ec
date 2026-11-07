/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ESP_LOG_H
#define TEST_ESP_LOG_H

#define ESP_LOG_NONE 0

/** @brief Verify the diagnostic protocol suppresses SDK logging.
 * @param tag Logger selector, expected to cover all loggers.
 * @param level Requested quiet log level.
 */
void esp_log_level_set(const char *tag, int level);

#endif
