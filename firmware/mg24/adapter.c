/* SPDX-License-Identifier: BSD-2-Clause */
#include "adapter.h"
#include <limits.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
	       "ISR counters require lock-free atomics");
_Static_assert(UINT_MAX == UINT32_MAX, "32-bit wrapping counters required");
static atomic_uint counts[EC_MG24_ADAPTER_COUNT];

void ec_mg24_adapter_reset(void)
{
	for (unsigned i = 0; i < EC_MG24_ADAPTER_COUNT; i++)
		atomic_store_explicit(&counts[i], 0, memory_order_relaxed);
}

void ec_mg24_adapter_count(enum ec_mg24_adapter_counter counter)
{
	if ((unsigned)counter < EC_MG24_ADAPTER_COUNT)
		atomic_fetch_add_explicit(&counts[counter], 1,
					  memory_order_relaxed);
}

size_t ec_mg24_adapter(char *out, size_t capacity)
{
	unsigned snapshot[EC_MG24_ADAPTER_COUNT];
	int length;

	for (unsigned i = 0; i < EC_MG24_ADAPTER_COUNT; i++)
		snapshot[i] =
			atomic_load_explicit(&counts[i], memory_order_relaxed);
	length = snprintf(
		out, capacity,
		"OK rx_event=%u hold_ok=%u copy_calls=%u release_ok=%u "
		"release_fail=%u core_calls=%u poll_budget=%u "
		"counter_bits=32 counter_mode=wrapping "
		"scope=last_start_attempt",
		snapshot[EC_MG24_ADAPTER_RX_EVENT],
		snapshot[EC_MG24_ADAPTER_HOLD_OK],
		snapshot[EC_MG24_ADAPTER_COPY_CALLS],
		snapshot[EC_MG24_ADAPTER_RELEASE_OK],
		snapshot[EC_MG24_ADAPTER_RELEASE_FAIL],
		snapshot[EC_MG24_ADAPTER_CORE_CALLS],
		snapshot[EC_MG24_ADAPTER_POLL_BUDGET]);
	return length > 0 && (size_t)length < capacity ? (size_t)length : 0;
}
