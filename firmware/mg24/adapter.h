/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_MG24_ADAPTER_H
#define EC_MG24_ADAPTER_H
#include <stddef.h>

/* Handoff occurrences, not unique frames or portable queue admissions. */
enum ec_mg24_adapter_counter {
	/* Existing RECEIVED event-bit occurrences, not a packet denominator. */
	EC_MG24_ADAPTER_RX_EVENT,
	/* Existing HoldRxPacket calls returning a valid handle. */
	EC_MG24_ADAPTER_HOLD_OK,
	/* Existing void CopyRxPacket calls; no success status is available. */
	EC_MG24_ADAPTER_COPY_CALLS,
	/* Existing ReleaseRxPacket calls returning success. */
	EC_MG24_ADAPTER_RELEASE_OK,
	/* Existing release errors; no retry or extra aggregate drop is added. */
	EC_MG24_ADAPTER_RELEASE_FAIL,
	/* Attempts to call ec_rf_receive, including inactive edge cases. */
	EC_MG24_ADAPTER_CORE_CALLS,
	/* Poll passes processing four handles; no fifth-packet probe. */
	EC_MG24_ADAPTER_POLL_BUDGET,
	EC_MG24_ADAPTER_COUNT,
};

/** @brief Reset the epoch while stopped, before each validated start attempt.
 * Failed attempts also reset these counters; RFQUEUE uses a different scope.
 */
void ec_mg24_adapter_reset(void);
/** @brief Increment one wrapping 32-bit handoff count without radio access.
 * @param counter Handoff occurrence; values outside the enum are ignored.
 */
void ec_mg24_adapter_count(enum ec_mg24_adapter_counter counter);
/** @brief Format a read-only snapshot after the core verifies stop and drain.
 * @param out Caller-owned response storage, including its terminating NUL.
 * @param capacity Writable bytes; zero permits no writes.
 * @return Complete response length, or zero when the buffer is too small.
 */
size_t ec_mg24_adapter(char *out, size_t capacity);
#endif
