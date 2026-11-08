/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ADAPTER_RAIL_H
#define TEST_ADAPTER_RAIL_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef void *RAIL_Handle_t;
typedef unsigned RAIL_Events_t;
typedef int RAIL_RxPacketHandle_t;
typedef int RAIL_Status_t;
typedef struct {
	void (*eventsCallback)(RAIL_Handle_t, RAIL_Events_t);
} RAIL_Config_t;
typedef struct {
	unsigned a, b;
} RAIL_StateTransitions_t;
typedef struct {
	unsigned mode, voltage, rampTime;
} RAIL_TxPowerConfig_t;
typedef struct {
	unsigned unused;
} RAIL_CsmaConfig_t;
typedef struct {
	unsigned packetStatus, packetBytes;
} RAIL_RxPacketInfo_t;
typedef struct {
	int rssi;
	unsigned lqi, crcPassed;
} RAIL_RxPacketDetails_t;
#define RAIL_RX_PACKET_HANDLE_INVALID (-1)
#define RAIL_RX_PACKET_HANDLE_OLDEST_COMPLETE 0
#define RAIL_RX_PACKET_READY_SUCCESS 1
#define RAIL_RX_PACKET_READY_CRC_ERROR 2
#define RAIL_STATUS_NO_ERROR 0
#define RAIL_RF_STATE_IDLE 0
#define RAIL_RF_STATE_RX 1
#define RAIL_IDLE_ABORT 1
#define RAIL_EVENT_RX_PACKET_RECEIVED 1U
#define RAIL_EVENT_RX_FIFO_OVERFLOW 2U
#define RAIL_EVENT_RX_FRAME_ERROR 4U
#define RAIL_EVENT_CAL_NEEDED 8U
#define RAIL_EVENT_TX_PACKET_SENT 16U
#define RAIL_EVENTS_TX_COMPLETION 48U
#define RAIL_EVENTS_ALL 0xffffU
#define RAIL_TX_POWER_MODE_2P4GIG_HP 0
#define RAIL_RX_OPTION_STORE_CRC 1
#define RAIL_CAL_ALL 1
#define RAIL_CAL_ALL_PENDING 1
#define RAIL_TX_OPTIONS_DEFAULT 0
#define RAIL_CSMA_CONFIG_SINGLE_CCA { 0 }
#define RAIL_GetTime() 123U
#define RAIL_HoldRxPacket(h) ((void)(h), test_hold_result)
#define RAIL_Idle(h, m, w) ((void)(h), (void)(m), (void)(w))
#define RAIL_GetRadioState(h) ((void)(h), RAIL_RF_STATE_IDLE)
#define RAIL_Init(c, cb) ((void)(c), (void)(cb), (void *)1)
#define RAIL_StartRx(h, ch, c)                                                 \
	((void)(h), (void)(ch), (void)(c), test_start_result)
#define RAIL_ConfigRxOptions(h, m, v) ((void)(h), (void)(m), (void)(v), 0)
#define RAIL_SetRxFifo(h, p, s) ((void)(h), (void)(p), (void)(s), 0)
#define RAIL_SetTxFifo(h, p, o, s) ((void)(h), (void)(p), (void)(o), (s))
#define RAIL_InitTxPowerCurvesAlt(p) ((void)(p), 0)
#define RAIL_ConfigTxPower(h, p) ((void)(h), (void)(p), 0)
#define RAIL_SetTxPowerDbm(h, p) ((void)(h), (void)(p), 0)
#define RAIL_GetTxPowerDbm(h) ((void)(h), 0)
#define RAIL_SetRxTransitions(h, p) ((void)(h), (void)(p), 0)
#define RAIL_SetTxTransitions(h, p) ((void)(h), (void)(p), 0)
#define RAIL_ConfigCal(h, c) ((void)(h), (void)(c), 0)
#define RAIL_ConfigEvents(h, m, v) ((void)(h), (void)(m), (void)(v), 0)
#define RAIL_WriteTxFifo(h, p, n, r) ((void)(h), (void)(p), (void)(r), (n))
#define RAIL_StartCcaCsmaTx(h, c, o, p, s)                                     \
	((void)(h), (void)(c), (void)(o), (void)(p), (void)(s), 0)
#define RAIL_StartTx(h, c, o, s) ((void)(h), (void)(c), (void)(o), (void)(s), 0)
#define RAIL_Calibrate(h, p, c) ((void)(h), (void)(p), (void)(c), 0)
#define RAIL_GetRxPacketInfo(h, p, i)                                          \
	((void)(h), (void)(p), test_packet_info(i))
#define RAIL_GetRxPacketDetailsAlt(h, p, d)                                    \
	((void)(h), (void)(p), test_packet_details(d))
#define RAIL_CopyRxPacket(b, i) ((void)(i), test_packet_copy(b))
#define RAIL_ReleaseRxPacket(h, p) ((void)(h), (void)(p), test_packet_release())
#endif
