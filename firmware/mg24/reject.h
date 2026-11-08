/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_MG24_REJECT_H
#define EC_MG24_REJECT_H

#include <stddef.h>
#include <stdint.h>

#define EC_MG24_REJECT_LIMIT 8u
#define EC_MG24_REJECT_BYTES 128u

struct ec_mg24_reject_metadata {
	uint32_t status, packet_bytes, details_available, crc_passed;
	int32_t rssi;
	uint32_t lqi, ticks;
};

/** @brief Clear bounded untrusted samples while RF is inactive before a start. */
void ec_mg24_reject_reset(void);

/** @brief Retain at most eight untrusted records without entering the valid RX path.
 * @param metadata RAIL status/length and optional details; NULL is counted invalid.
 * @param data Raw PHR plus received bytes, never trusted source identity.
 * @param copied Available bytes; clamped to 128, must not exceed packet_bytes.
 * Counters saturate at UINT32_MAX. Invalid input is omitted; truncated input is
 * retained with its actual length. Details flags must be 0/1; unavailable details
 * do not invalidate raw bytes. First-eight storage never overwrites or drains.
 */
void ec_mg24_reject_record(const struct ec_mg24_reject_metadata *metadata,
			   const uint8_t *data, size_t copied);

/** @brief Format one immutable untrusted record after the caller verifies idle.
 * @param index Record index, 0..7; absent/out-of-range indices return an error body.
 * @param out Bounded response body storage.
 * @param capacity Available bytes including terminating NUL.
 * @return Complete body length, or zero if storage is insufficient.
 * Fields: diag=untrusted_rx index status packet_bytes stored_bytes truncated
 * layout_complete details crc_passed rssi lqi ticks fcs data. layout_complete
 * means only internally consistent PHR/length; fcs=original then identifies the
 * retained final two bytes, not trusted validity. Otherwise fcs=unavailable.
 * Unavailable details print crc_passed=unknown, rssi=unknown and lqi=unknown.
 */
size_t ec_mg24_reject_read(unsigned index, char *out, size_t capacity);

/** @brief Append bounded saturating reject counters without changing the prefix.
 * @param out Existing response body storage, NUL-terminated at prefix_length.
 * @param capacity Total available bytes including NUL.
 * @param prefix_length Existing complete prefix length.
 * @return Total complete length, or zero if input/storage is invalid.
 * Adds reject_seen, reject_stored, reject_omitted, reject_truncated,
 * reject_invalid and reject_count_mode=saturating.
 */
size_t ec_mg24_reject_append_summary(char *out, size_t capacity,
				     size_t prefix_length);

/** @brief Fence private diagnostic TX before any core/adapter side effects.
 * @param command Complete command after the request ID.
 * @param out Bounded response body storage.
 * @param capacity Available bytes including NUL.
 * @return Complete body length, or zero if storage is insufficient.
 * RFSTART/RFTX always return ERR RF_RX_ONLY. RFREJECT accepts exactly one index
 * digit0..7 only while core idle; RFLISTEN also requires idle before delegation.
 * Other commands delegate unchanged to ec_rf_command.
 */
size_t ec_mg24_reject_command(const char *command, char *out, size_t capacity);

#ifdef EC_MG24_REJECT_TEST
/** @brief Seed only saturating counters for native overflow regressions.
 * @param value Counter value; record storage/count remains untouched.
 */
void ec_mg24_reject_test_seed(uint32_t value);
#endif

#endif
