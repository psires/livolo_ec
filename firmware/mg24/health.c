/* SPDX-License-Identifier: BSD-2-Clause */
#include "health.h"
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#ifdef EC_RF_REJECT_DIAGNOSTICS
#include "reject.h"
#endif

_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
	       "ISR counters require lock-free atomics");
_Static_assert(sizeof(unsigned) == sizeof(uint32_t),
	       "32-bit counters required");
static atomic_uint reasons[EC_MG24_REASON_COUNT];

void ec_mg24_health_reset(void)
{
	for (unsigned i = 0; i < EC_MG24_REASON_COUNT; i++)
		atomic_store_explicit(&reasons[i], 0, memory_order_relaxed);
}

void ec_mg24_health_count(enum ec_mg24_rx_reason reason)
{
	if ((unsigned)reason < EC_MG24_REASON_COUNT)
		atomic_fetch_add_explicit(&reasons[reason], 1,
					  memory_order_relaxed);
}

size_t ec_mg24_health(char *out, size_t capacity)
{
	unsigned snapshot[EC_MG24_REASON_COUNT];
	int length;

	for (unsigned i = 0; i < EC_MG24_REASON_COUNT; i++)
		snapshot[i] =
			atomic_load_explicit(&reasons[i], memory_order_relaxed);
	length = snprintf(
		out, capacity,
		"OK rx_hold_failed=%u rx_fifo_overflow=%u rx_frame_error=%u "
		"rx_packet_status_invalid=%u rx_packet_length_invalid=%u "
		"rx_packet_details_failed=%u rx_packet_layout_invalid=%u "
		"rx_calibration_failed=%u counter_bits=32 "
		"scope=last_start_attempt",
		snapshot[EC_MG24_HOLD_FAILED], snapshot[EC_MG24_FIFO_OVERFLOW],
		snapshot[EC_MG24_FRAME_ERROR],
		snapshot[EC_MG24_PACKET_STATUS_INVALID],
		snapshot[EC_MG24_PACKET_LENGTH_INVALID],
		snapshot[EC_MG24_PACKET_DETAILS_FAILED],
		snapshot[EC_MG24_PACKET_LAYOUT_INVALID],
		snapshot[EC_MG24_CALIBRATION_FAILED]);
	if (length <= 0 || (size_t)length >= capacity)
		return 0;
#ifdef EC_RF_REJECT_DIAGNOSTICS
	return ec_mg24_reject_append_summary(out, capacity, length);
#else
	return length;
#endif
}
