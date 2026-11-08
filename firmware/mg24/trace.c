/* SPDX-License-Identifier: BSD-2-Clause */
#include "trace.h"
#include "rf.h"
#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

struct event_record {
	uint32_t sequence, ticks, mask_low, mask_high;
};
struct anchor_record {
	struct ec_mg24_trace_metadata metadata;
	uint32_t sequence, length;
	uint8_t data[127];
};
struct mark_record {
	uint32_t sequence, kind, ticks, result;
};
static struct event_record events[EC_MG24_TRACE_EVENTS];
static struct anchor_record anchors[EC_MG24_TRACE_ANCHORS];
static struct mark_record marks[EC_MG24_TRACE_MARKS];
static atomic_uint recording, event_seen, event_stored, event_omitted;
static atomic_uint callback_max_us;
static atomic_uint callback_busy, callback_reentry;
static uint32_t epoch, anchor_seen, anchor_stored, anchor_omitted,
	anchor_invalid;
static uint32_t mark_seen, mark_stored, mark_omitted;
_Static_assert(ATOMIC_INT_LOCK_FREE == 2 && UINT_MAX == UINT32_MAX,
	       "Trace publication requires lock-free 32-bit unsigned");
_Static_assert(sizeof(struct event_record) == 16, "Trace event storage bound");
_Static_assert(sizeof(events) + sizeof(anchors) + sizeof(marks) <= 160 * 1024,
	       "Trace RAM budget exceeded");

/** @brief Increment one main-loop-owned counter without wrapping.
 * @param value Counter storage.
 */
static void increment(uint32_t *value)
{
	if (*value != UINT32_MAX)
		++*value;
}

void ec_mg24_trace_reset(void)
{
	atomic_store(&recording, 0);
	epoch++;
	atomic_store(&event_seen, 0);
	atomic_store(&event_stored, 0);
	atomic_store(&event_omitted, 0);
	atomic_store(&callback_max_us, 0);
	atomic_store(&callback_busy, 0);
	atomic_store(&callback_reentry, 0);
	anchor_seen = anchor_stored = anchor_omitted = anchor_invalid = 0;
	mark_seen = mark_stored = mark_omitted = 0;
	/* Slots beyond published counts are inaccessible; avoid a large memset. */
	atomic_store_explicit(&recording, 1, memory_order_release);
}

void ec_mg24_trace_freeze(void)
{
	atomic_store_explicit(&recording, 0, memory_order_release);
}

int ec_mg24_trace_callback_enter(void)
{
	unsigned expected = 0;
	if (!atomic_load_explicit(&recording, memory_order_acquire))
		return 0;
	/* One reservation attempt, including a conservative spurious-failure flag. */
	if (!atomic_compare_exchange_weak_explicit(&callback_busy, &expected, 1,
						   memory_order_acquire,
						   memory_order_relaxed)) {
		atomic_store_explicit(&callback_reentry, 1,
				      memory_order_relaxed);
		return 0;
	}
	return 1;
}

void ec_mg24_trace_event(uint32_t ticks, uint64_t mask)
{
	unsigned seen, stored;

	if (!atomic_load_explicit(&recording, memory_order_acquire))
		return;
	seen = atomic_load_explicit(&event_seen, memory_order_relaxed);
	if (seen != UINT32_MAX)
		seen++;
	atomic_store_explicit(&event_seen, seen, memory_order_relaxed);
	stored = atomic_load_explicit(&event_stored, memory_order_relaxed);
	if (stored == EC_MG24_TRACE_EVENTS) {
		unsigned omitted = atomic_load_explicit(&event_omitted,
							memory_order_relaxed);
		if (omitted != UINT32_MAX)
			atomic_store_explicit(&event_omitted, omitted + 1,
					      memory_order_relaxed);
		return;
	}
	events[stored] = (struct event_record){ seen, ticks, (uint32_t)mask,
						(uint32_t)(mask >> 32) };
	atomic_store_explicit(&event_stored, stored + 1, memory_order_release);
}

void ec_mg24_trace_callback_cost(uint32_t elapsed)
{
	if (atomic_load_explicit(&recording, memory_order_acquire) &&
	    elapsed > atomic_load_explicit(&callback_max_us,
					   memory_order_relaxed))
		atomic_store_explicit(&callback_max_us, elapsed,
				      memory_order_relaxed);
	atomic_store_explicit(&callback_busy, 0, memory_order_release);
}

void ec_mg24_trace_mark(enum ec_mg24_trace_mark kind, uint32_t ticks,
			uint32_t result)
{
	if (!atomic_load_explicit(&recording, memory_order_acquire))
		return;
	increment(&mark_seen);
	if (mark_stored == EC_MG24_TRACE_MARKS) {
		increment(&mark_omitted);
		return;
	}
	marks[mark_stored++] =
		(struct mark_record){ mark_seen, kind, ticks, result };
}

void ec_mg24_trace_anchor(const struct ec_mg24_trace_metadata *metadata,
			  const uint8_t *data, size_t length)
{
	struct anchor_record *record;

	increment(&anchor_seen);
	if (!metadata || !data || length < 3 || length > 127 ||
	    metadata->time_valid > 1 || metadata->end_valid > 1 ||
	    metadata->air_bytes != length + 1) {
		increment(&anchor_invalid);
		increment(&anchor_omitted);
		return;
	}
	if (anchor_stored == EC_MG24_TRACE_ANCHORS) {
		increment(&anchor_omitted);
		return;
	}
	record = &anchors[anchor_stored++];
	record->metadata = *metadata;
	record->sequence = anchor_seen;
	record->length = (uint32_t)length;
	memcpy(record->data, data, length);
}

/** @brief Return a complete fixed response or refuse an insufficient buffer.
 * @param text Constant response body.
 * @param out Destination.
 * @param capacity Bytes available including NUL.
 * @return Complete length or zero.
 */
static size_t reply(const char *text, char *out, size_t capacity)
{
	size_t length = strlen(text);
	if (!out || length >= capacity)
		return 0;
	memcpy(out, text, length + 1);
	return length;
}

/** @brief Parse canonical decimal record indices without overflow.
 * @param text Entire index token.
 * @param value Parsed output on success.
 * @return Nonzero for valid tokens up to and including UINT32_MAX.
 */
static int index_token(const char *text, unsigned *value)
{
	unsigned n = 0;
	if (!*text || (*text == '0' && text[1]))
		return 0;
	for (; *text; text++) {
		unsigned digit = (unsigned)(*text - '0');
		if (digit > 9 || n > (UINT32_MAX - digit) / 10)
			return 0;
		n = n * 10 + digit;
	}
	*value = n;
	return 1;
}

/** @brief Format stable trace metadata after the caller proves frozen idle.
 * @param out Destination response buffer.
 * @param capacity Bytes including NUL.
 * @return Complete length or zero.
 */
static size_t summary(char *out, size_t capacity)
{
	int n = snprintf(
		out, capacity,
		"OK trace=events_v1 epoch=%u frozen=1 ticks_hz=1000000 "
		"ticks_bits=32 event_origin=callback anchor_origin=rail_packet "
		"counter_mode=saturating event_seen=%u event_stored=%u "
		"event_omitted=%u "
		"anchor_seen=%u anchor_stored=%u anchor_omitted=%u "
		"anchor_invalid=%u "
		"mark_seen=%u mark_stored=%u mark_omitted=%u "
		"callback_max_us=%u reentry=%u",
		epoch, atomic_load(&event_seen), atomic_load(&event_stored),
		atomic_load(&event_omitted), anchor_seen, anchor_stored,
		anchor_omitted, anchor_invalid, mark_seen, mark_stored,
		mark_omitted, atomic_load(&callback_max_us),
		atomic_load(&callback_reentry));
	return n > 0 && (size_t)n < capacity ? (size_t)n : 0;
}

/** @brief Format one bounded immutable record without consuming it.
 * @param kind EVENT, ANCHOR or MARK selector.
 * @param index Zero-based stored-record index.
 * @param out Destination response buffer.
 * @param capacity Available bytes including NUL.
 * @return Complete length or zero.
 */
static size_t record_read(unsigned kind, unsigned index, char *out,
			  size_t capacity)
{
	int n;
	if (kind == 0 && index < atomic_load(&event_stored)) {
		const struct event_record *r = &events[index];
		n = snprintf(out, capacity,
			     "OK trace=event epoch=%u index=%u seq=%u ticks=%u "
			     "mask=%08x%08x",
			     epoch, index, r->sequence, r->ticks, r->mask_high,
			     r->mask_low);
	} else if (kind == 1 && index < anchor_stored) {
		const struct anchor_record *r = &anchors[index];
		const struct ec_mg24_trace_metadata *m = &r->metadata;
		n = snprintf(out, capacity,
			     "OK trace=anchor epoch=%u index=%u seq=%u "
			     "poll_ticks=%u "
			     "packet_ticks=%u time_position=%u time_valid=%u "
			     "status=%u "
			     "air_bytes=%u len=%u end_ticks=%u end_valid=%u "
			     "end_status=%u fcs=original data=",
			     epoch, index, r->sequence, m->poll_ticks,
			     m->packet_ticks, m->time_position, m->time_valid,
			     m->packet_status, m->air_bytes, r->length,
			     m->end_ticks, m->end_valid, m->end_status);
		if (n <= 0 || (size_t)n >= capacity ||
		    r->length * 2 >= capacity - (size_t)n)
			return 0;
		static const char hex[] = "0123456789abcdef";
		for (unsigned i = 0; i < r->length; i++) {
			out[n++] = hex[r->data[i] >> 4];
			out[n++] = hex[r->data[i] & 15];
		}
		out[n] = 0;
	} else if (kind == 2 && index < mark_stored) {
		const struct mark_record *r = &marks[index];
		n = snprintf(out, capacity,
			     "OK trace=mark epoch=%u index=%u seq=%u kind=%u "
			     "ticks=%u result=%u",
			     epoch, index, r->sequence, r->kind, r->ticks,
			     r->result);
	} else {
		return reply("ERR RF_TRACE_INDEX", out, capacity);
	}
	return n > 0 && (size_t)n < capacity ? (size_t)n : 0;
}

size_t ec_mg24_trace_command(const char *command, char *out, size_t capacity)
{
	static const char *const prefixes[] = { "RFTRACE EVENT ",
						"RFTRACE ANCHOR ",
						"RFTRACE MARK " };
	if (!command || !out || !capacity)
		return 0;
	if (!strncmp(command, "RFSTART", 7) || !strncmp(command, "RFTX", 4))
		return reply("ERR RF_RX_ONLY", out, capacity);
	if (!strncmp(command, "RFTRACE", 7)) {
		if (atomic_load_explicit(&recording, memory_order_acquire) ||
		    !ec_rf_diagnostic_idle())
			return reply("ERR RF_BUSY_OR_FAULT", out, capacity);
		if (!strcmp(command, "RFTRACE"))
			return summary(out, capacity);
		for (unsigned kind = 0; kind < 3; kind++) {
			size_t length = strlen(prefixes[kind]);
			unsigned index;
			if (!strncmp(command, prefixes[kind], length) &&
			    index_token(command + length, &index))
				return record_read(kind, index, out, capacity);
		}
		return reply("ERR RF_TRACE_SYNTAX", out, capacity);
	}
	if (!strncmp(command, "RFLISTEN", 8) && !ec_rf_diagnostic_idle())
		return reply("ERR RF_BUSY_OR_FAULT", out, capacity);
	return ec_rf_command(command, out, capacity);
}
