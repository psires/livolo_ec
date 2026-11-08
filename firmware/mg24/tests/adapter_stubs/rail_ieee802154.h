/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ADAPTER_IEEE_H
#define TEST_ADAPTER_IEEE_H
#include "rail.h"
typedef struct {
	struct {
		bool enable;
		RAIL_StateTransitions_t rxTransitions, txTransitions;
	} ackConfig;
	struct {
		unsigned idleToRx, idleToTx, rxToTx, txToRx;
	} timings;
	unsigned framesMask;
	bool promiscuousMode;
} RAIL_IEEE802154_Config_t;
#define RAIL_IEEE802154_ACCEPT_STANDARD_FRAMES 0xff
#define RAIL_IEEE802154_Config2p4GHzRadio(h) ((void)(h), 0)
#define RAIL_IEEE802154_Init(h, c) ((void)(h), (void)(c), 0)
#endif
