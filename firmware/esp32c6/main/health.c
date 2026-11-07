/* SPDX-License-Identifier: BSD-2-Clause */
#include "health.h"
#include "rf.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
	       "Watchdog hook requires lock-free uint");
static uint32_t reason, loops, read_calls, read_bytes, read_zero, read_error;
static uint32_t write_calls, written_bytes, write_zero, write_error, unsent;
static atomic_uint task_wdt_events;

void ec_c6_health_init(void)
{
	reason = (uint32_t)esp_reset_reason();
	loops = read_calls = read_bytes = read_zero = read_error = 0;
	write_calls = written_bytes = write_zero = write_error = unsent = 0;
	atomic_store_explicit(&task_wdt_events, 0, memory_order_relaxed);
}

void ec_c6_health_loop(size_t pending)
{
	loops++;
	unsent = (uint32_t)pending;
}

void ec_c6_health_read(int result)
{
	read_calls++;
	if (result > 0)
		read_bytes += (uint32_t)result;
	else if (!result)
		read_zero++;
	else
		read_error++;
}

void ec_c6_health_write(int result)
{
	write_calls++;
	if (result > 0)
		written_bytes += (uint32_t)result;
	else if (!result)
		write_zero++;
	else
		write_error++;
}

/** @brief Count task watchdog reports in ISR context; never log, feed or reset. */
void EC_RF_ISR_ATTR esp_task_wdt_isr_user_handler(void)
{
	atomic_fetch_add_explicit(&task_wdt_events, 1, memory_order_relaxed);
}

size_t ec_c6_health(char *out, size_t capacity)
{
	uint32_t uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
	unsigned stack = (unsigned)uxTaskGetStackHighWaterMark(NULL);
	int length = snprintf(
		out, capacity,
		"OK uptime_ms=%lu reset_reason=%lu loops=%lu "
		"read_calls=%lu read_bytes=%lu read_zero=%lu read_error=%lu "
		"write_calls=%lu written_bytes=%lu write_zero=%lu "
		"write_error=%lu "
		"pending=%lu mainstack_min_bytes=%u task_wdt_events=%u "
		"counter_bits=32",
		(unsigned long)uptime_ms, (unsigned long)reason,
		(unsigned long)loops, (unsigned long)read_calls,
		(unsigned long)read_bytes, (unsigned long)read_zero,
		(unsigned long)read_error, (unsigned long)write_calls,
		(unsigned long)written_bytes, (unsigned long)write_zero,
		(unsigned long)write_error, (unsigned long)unsent, stack,
		atomic_load_explicit(&task_wdt_events, memory_order_relaxed));

	return length > 0 && (size_t)length < capacity ? (size_t)length : 0;
}
