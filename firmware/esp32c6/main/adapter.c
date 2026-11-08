/* SPDX-License-Identifier: BSD-2-Clause */
#include "adapter.h"
#include <limits.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
	       "ISR counters require lock-free atomics");
_Static_assert(UINT_MAX == UINT32_MAX, "32-bit wrapping counters required");
static atomic_uint counts[EC_C6_ADAPTER_COUNT];

void ec_c6_adapter_reset(void)
{
	for (unsigned i = 0; i < EC_C6_ADAPTER_COUNT; i++)
		atomic_store_explicit(&counts[i], 0, memory_order_relaxed);
}

void ec_c6_adapter_count(enum ec_c6_adapter_counter counter)
{
	if ((unsigned)counter < EC_C6_ADAPTER_COUNT)
		atomic_fetch_add_explicit(&counts[counter], 1,
					  memory_order_relaxed);
}

size_t ec_c6_adapter(char *out, size_t capacity)
{
	unsigned snapshot[EC_C6_ADAPTER_COUNT];
	int length;

	for (unsigned i = 0; i < EC_C6_ADAPTER_COUNT; i++)
		snapshot[i] =
			atomic_load_explicit(&counts[i], memory_order_relaxed);
	length = snprintf(out, capacity,
			  "OK sfd_event=%u rx_done=%u rx_invalid=%u rx_null=%u "
			  "rx_release_ok=%u rx_release_fail=%u "
			  "ack_release_ok=%u ack_release_fail=%u core_calls=%u "
			  "counter_bits=32 counter_mode=wrapping "
			  "scope=last_start_attempt",
			  snapshot[EC_C6_ADAPTER_SFD_EVENT],
			  snapshot[EC_C6_ADAPTER_RX_DONE],
			  snapshot[EC_C6_ADAPTER_RX_INVALID],
			  snapshot[EC_C6_ADAPTER_RX_NULL],
			  snapshot[EC_C6_ADAPTER_RX_RELEASE_OK],
			  snapshot[EC_C6_ADAPTER_RX_RELEASE_FAIL],
			  snapshot[EC_C6_ADAPTER_ACK_RELEASE_OK],
			  snapshot[EC_C6_ADAPTER_ACK_RELEASE_FAIL],
			  snapshot[EC_C6_ADAPTER_CORE_CALLS]);
	return length > 0 && (size_t)length < capacity ? (size_t)length : 0;
}
