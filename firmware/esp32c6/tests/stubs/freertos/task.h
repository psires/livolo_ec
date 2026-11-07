/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_FREERTOS_TASK_H
#define TEST_FREERTOS_TASK_H

#include <stdint.h>

/** @brief Record bounded yielding while the adapter retries transport I/O.
 * @param ticks Nonzero delay requested by the actual adapter loop.
 */
void vTaskDelay(uint32_t ticks);

/** @brief Return a synthetic stack watermark without querying a real task.
 * @param task NULL selects the current mock task.
 * @return Mock minimum unused stack bytes under ESP-IDF semantics.
 */
unsigned uxTaskGetStackHighWaterMark(void *task);

#endif
