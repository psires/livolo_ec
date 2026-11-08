/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ADAPTER_GPIO_H
#define TEST_ADAPTER_GPIO_H
#include <stdint.h>
typedef struct {
	uint64_t pin_bit_mask;
	unsigned mode;
} gpio_config_t;
#define GPIO_MODE_OUTPUT 1
#define gpio_config(p) ((void)(p), 0)
#define gpio_set_level(p, v) ((void)(p), (void)(v), 0)
#endif
