/* SPDX-License-Identifier: BSD-2-Clause */
#include "reject.h"
#include "rf.h"
#include <stdio.h>
#include <string.h>

struct reject_record {
	struct ec_mg24_reject_metadata metadata;
	uint8_t data[EC_MG24_REJECT_BYTES];
	unsigned copied, truncated;
};
static struct reject_record records[EC_MG24_REJECT_LIMIT];
static uint32_t seen, stored, omitted, truncated, invalid;

/** @brief Increment diagnostic evidence without wrapping to a misleading zero.
 * @param counter Main-loop-owned saturating counter.
 */
static void increment(uint32_t *counter)
{
	if (*counter != UINT32_MAX)
		(*counter)++;
}

/** @brief Return a complete fixed diagnostic error, never a partial body.
 * @param message NUL-terminated constant response.
 * @param out Response storage.
 * @param capacity Bytes available including NUL.
 * @return Complete length or zero if storage is insufficient.
 */
static size_t response(const char *message, char *out, size_t capacity)
{
	size_t length = strlen(message);

	if (!out || length >= capacity)
		return 0;
	memcpy(out, message, length + 1);
	return length;
}

void ec_mg24_reject_reset(void)
{
	seen = stored = omitted = truncated = invalid = 0;
	memset(records, 0, sizeof(records));
}

void ec_mg24_reject_record(const struct ec_mg24_reject_metadata *metadata,
			   const uint8_t *data, size_t copied)
{
	struct reject_record *record;
	unsigned cut;

	increment(&seen);
	if (!metadata || (copied && !data) || copied > metadata->packet_bytes ||
	    metadata->details_available > 1 || metadata->crc_passed > 1) {
		increment(&invalid);
		increment(&omitted);
		return;
	}
	cut = copied > EC_MG24_REJECT_BYTES || copied < metadata->packet_bytes;
	if (cut)
		increment(&truncated);
	if (stored == EC_MG24_REJECT_LIMIT) {
		increment(&omitted);
		return;
	}
	if (copied > EC_MG24_REJECT_BYTES)
		copied = EC_MG24_REJECT_BYTES;
	record = &records[stored++];
	record->metadata = *metadata;
	record->copied = copied;
	record->truncated = cut;
	if (copied)
		memcpy(record->data, data, copied);
}

size_t ec_mg24_reject_read(unsigned index, char *out, size_t capacity)
{
	static const char hex[] = "0123456789abcdef";
	const struct reject_record *record;
	const struct ec_mg24_reject_metadata *metadata;
	char crc[8], rssi[16], lqi[16];
	unsigned complete;
	int length;
	size_t used;

	if (index >= EC_MG24_REJECT_LIMIT)
		return response("ERR RF_REJECT_INDEX", out, capacity);
	if (index >= stored)
		return response("ERR RF_REJECT_ABSENT", out, capacity);
	if (!out || !capacity)
		return 0;
	record = &records[index];
	metadata = &record->metadata;
	complete = record->copied >= 3 && !record->truncated &&
		   !(record->data[0] & 0x80) &&
		   (unsigned)record->data[0] + 1 == record->copied &&
		   record->copied == metadata->packet_bytes;
	if (metadata->details_available) {
		snprintf(crc, sizeof(crc), "%lu",
			 (unsigned long)metadata->crc_passed);
		snprintf(rssi, sizeof(rssi), "%ld", (long)metadata->rssi);
		snprintf(lqi, sizeof(lqi), "%lu", (unsigned long)metadata->lqi);
	} else {
		strcpy(crc, "unknown");
		strcpy(rssi, "unknown");
		strcpy(lqi, "unknown");
	}
	length = snprintf(
		out, capacity,
		"OK diag=untrusted_rx index=%u status=%lu packet_bytes=%lu "
		"stored_bytes=%u truncated=%u layout_complete=%u details=%lu "
		"crc_passed=%s rssi=%s lqi=%s ticks=%lu fcs=%s data=",
		index, (unsigned long)metadata->status,
		(unsigned long)metadata->packet_bytes, record->copied,
		record->truncated, complete,
		(unsigned long)metadata->details_available, crc, rssi, lqi,
		(unsigned long)metadata->ticks,
		complete ? "original" : "unavailable");
	if (length < 0 || (size_t)length >= capacity ||
	    record->copied * 2 >= capacity - (size_t)length)
		return 0;
	used = length;
	for (unsigned i = 0; i < record->copied; i++) {
		out[used++] = hex[record->data[i] >> 4];
		out[used++] = hex[record->data[i] & 15];
	}
	out[used] = 0;
	return used;
}

size_t ec_mg24_reject_append_summary(char *out, size_t capacity,
				     size_t prefix_length)
{
	int length;

	if (!out || prefix_length >= capacity || out[prefix_length])
		return 0;
	length = snprintf(
		out + prefix_length, capacity - prefix_length,
		" reject_seen=%lu reject_stored=%lu reject_omitted=%lu "
		"reject_truncated=%lu reject_invalid=%lu "
		"reject_count_mode=saturating",
		(unsigned long)seen, (unsigned long)stored,
		(unsigned long)omitted, (unsigned long)truncated,
		(unsigned long)invalid);
	return length > 0 && (size_t)length < capacity - prefix_length
		       ? prefix_length + (size_t)length
		       : 0;
}

size_t ec_mg24_reject_command(const char *command, char *out, size_t capacity)
{
	if (!strncmp(command, "RFSTART", 7) || !strncmp(command, "RFTX", 4))
		return response("ERR RF_RX_ONLY", out, capacity);
	if (!strncmp(command, "RFREJECT", 8)) {
		if (strlen(command) != 10 || command[8] != ' ' ||
		    command[9] < '0' || command[9] > '7')
			return response("ERR RF_REJECT_INDEX", out, capacity);
		if (!ec_rf_diagnostic_idle())
			return response("ERR RF_BUSY_OR_FAULT", out, capacity);
		return ec_mg24_reject_read(command[9] - '0', out, capacity);
	}
	if (!strncmp(command, "RFLISTEN", 8) && !ec_rf_diagnostic_idle())
		return response("ERR RF_BUSY_OR_FAULT", out, capacity);
	return ec_rf_command(command, out, capacity);
}

#ifdef EC_MG24_REJECT_TEST
void ec_mg24_reject_test_seed(uint32_t value)
{
	seen = omitted = truncated = invalid = value;
}
#endif
