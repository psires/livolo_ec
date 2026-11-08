/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_FREERTOS_H
#define TEST_FREERTOS_H

/* Match target CONFIG_FREERTOS_HZ=100; no scheduler or hardware is instantiated. */
#define pdMS_TO_TICKS(value) ((value) / 10)

#endif
