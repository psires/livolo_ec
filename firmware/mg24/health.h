/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_MG24_HEALTH_H
#define EC_MG24_HEALTH_H

#include <stddef.h>

enum ec_mg24_rx_reason {
	EC_MG24_HOLD_FAILED,
	EC_MG24_FIFO_OVERFLOW,
	EC_MG24_FRAME_ERROR,
	EC_MG24_PACKET_STATUS_INVALID,
	EC_MG24_PACKET_LENGTH_INVALID,
	EC_MG24_PACKET_DETAILS_FAILED,
	EC_MG24_PACKET_LAYOUT_INVALID,
	EC_MG24_CALIBRATION_FAILED,
	EC_MG24_REASON_COUNT,
};

/** @brief Clear reason occurrences while RF is inactive, before a start attempt. */
void ec_mg24_health_reset(void);

/** @brief Count one diagnostic occurrence without changing aggregate drop policy.
 * @param reason Valid hardware or adapter discard reason; invalid values ignored.
 */
void ec_mg24_health_count(enum ec_mg24_rx_reason reason);

/** @brief Snapshot per-start reason counts without radio or counter side effects.
 * @param out Caller-owned response storage.
 * @param capacity Available bytes, including terminating NUL.
 * @return Complete response length, or zero if storage is insufficient.
 */
size_t ec_mg24_health(char *out, size_t capacity);

#endif
