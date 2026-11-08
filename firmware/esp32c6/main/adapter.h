/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_C6_ADAPTER_H
#define EC_C6_ADAPTER_H
#include <stddef.h>
#include "rf.h"

/* Handoff occurrences, not unique frames or portable queue admissions. */
enum ec_c6_adapter_counter {
	/* Already-enabled SFD callback entries, not unique received packets. */
	EC_C6_ADAPTER_SFD_EVENT,
	/* Receive-done callback entries, before argument validation. */
	EC_C6_ADAPTER_RX_DONE,
	/* Callbacks rejected by the existing argument or PHR checks. */
	EC_C6_ADAPTER_RX_INVALID,
	/* Null frame pointers; a subset of RX_INVALID with no release call. */
	EC_C6_ADAPTER_RX_NULL,
	/* Existing received-frame release calls returning success. */
	EC_C6_ADAPTER_RX_RELEASE_OK,
	/* Existing received-frame release errors; no new aggregate drop. */
	EC_C6_ADAPTER_RX_RELEASE_FAIL,
	/* Existing unexpected ACK release calls returning success. */
	EC_C6_ADAPTER_ACK_RELEASE_OK,
	/* Existing unexpected ACK release errors; no new aggregate drop. */
	EC_C6_ADAPTER_ACK_RELEASE_FAIL,
	/* Attempts to call ec_rf_receive, not successful queue admissions. */
	EC_C6_ADAPTER_CORE_CALLS,
	EC_C6_ADAPTER_COUNT,
};

/** @brief Reset the epoch while stopped, before each validated start attempt.
 * Failed attempts also reset these counters; RFQUEUE uses a different scope.
 */
void ec_c6_adapter_reset(void);
/** @brief Increment one wrapping 32-bit handoff count without radio access.
 * @param counter Handoff occurrence; values outside the enum are ignored.
 */
void EC_RF_ISR_ATTR ec_c6_adapter_count(enum ec_c6_adapter_counter counter);
/** @brief Format a read-only snapshot after the core verifies stop and drain.
 * @param out Caller-owned response storage, including its terminating NUL.
 * @param capacity Writable bytes; zero permits no writes.
 * @return Complete response length, or zero when the buffer is too small.
 */
size_t ec_c6_adapter(char *out, size_t capacity);
#endif
