/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_C6_HEALTH_H
#define EC_C6_HEALTH_H
#include <stddef.h>

/** @brief Reset volatile diagnostic counters and snapshot the SDK reset reason. */
void ec_c6_health_init(void);
/** @brief Record one main-loop iteration without logging or feeding a watchdog.
 * @param pending Number of reply/event bytes still unsent.
 */
void ec_c6_health_loop(size_t pending);
/** @brief Record a USB read result, including zero or negative returns.
 * @param result Driver return value, before adapter normalization.
 */
void ec_c6_health_read(int result);
/** @brief Record a USB write result, including zero or negative returns.
 * @param result Driver return value, before adapter normalization.
 */
void ec_c6_health_write(int result);
/** @brief Format read-only health evidence; uint32 counters wrap modulo 2^32.
 * @param out Reply-body storage, without RSP prefix or newline.
 * @param capacity Available bytes including the NUL terminator.
 * @return Complete body length, or zero if it does not fit.
 */
size_t ec_c6_health(char *out, size_t capacity);
#endif
