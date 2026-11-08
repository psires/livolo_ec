/* SPDX-License-Identifier: BSD-2-Clause */
#include "rf.h"
#include "health.h"
#include "em_gpio.h"
#include "rail.h"
#include "rail_ieee802154.h"
#include "pa_conversions_efr32.h"
#include <stdatomic.h>
#include <string.h>
#ifdef EC_RF_REJECT_DIAGNOSTICS
#include "reject.h"
#endif

static RAIL_Handle_t radio;
static unsigned selected_channel;
static atomic_uint calibration;
#ifndef EC_RF_REJECT_DIAGNOSTICS
static uint8_t tx_fifo[256] __attribute__((aligned(4)));
#endif
static uint8_t rx_fifo[2048] __attribute__((aligned(4)));

/** @brief Retain RX packets for bounded main-loop copying and publish TX status.
 * @param handle RAIL instance that owns received packet handles.
 * @param events Hardware event mask; no serial output occurs here.
 */
static void event(RAIL_Handle_t handle, RAIL_Events_t events)
{
	if (events & RAIL_EVENT_RX_PACKET_RECEIVED)
		if (RAIL_HoldRxPacket(handle) ==
		    RAIL_RX_PACKET_HANDLE_INVALID) {
			ec_mg24_health_count(EC_MG24_HOLD_FAILED);
			ec_rf_drop();
		}
	if (events & RAIL_EVENT_RX_FIFO_OVERFLOW)
		ec_mg24_health_count(EC_MG24_FIFO_OVERFLOW);
	if (events & RAIL_EVENT_RX_FRAME_ERROR)
		ec_mg24_health_count(EC_MG24_FRAME_ERROR);
	if (events & (RAIL_EVENT_RX_FIFO_OVERFLOW | RAIL_EVENT_RX_FRAME_ERROR))
		ec_rf_drop();
	if (events & RAIL_EVENT_CAL_NEEDED)
		atomic_store(&calibration, 1);
	if (events & RAIL_EVENTS_TX_COMPLETION)
		ec_rf_tx_complete((events & RAIL_EVENT_TX_PACKET_SENT) != 0);
}

/** @brief Return callback/poll microseconds from the running RAIL timer.
 * @return Zero before initialization, otherwise wrapping uint32 microseconds.
 */
static uint32_t now_us(void)
{
	return radio ? RAIL_GetTime() : 0;
}

/** @brief Synchronously abort current radio operations before buffer reuse.
 * @return Zero only if RAIL is idle after cancellation.
 */
static int stop(void)
{
	selected_channel = 0;
	if (radio)
		RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
	return radio && RAIL_GetRadioState(radio) != RAIL_RF_STATE_IDLE ? -1
									: 0;
}

/** @brief Configure raw IEEE PHY, retained FCS and no hardware ACKs.
 * @param channel Fixed passive channel 11 or synthetic channel 26.
 * @return Zero only after verified configuration and RX entry.
 */
static int start(unsigned channel)
{
	static RAIL_Config_t config = { .eventsCallback = event };
	RAIL_IEEE802154_Config_t ieee = {
		.ackConfig = { .enable = false,
			       .rxTransitions = { RAIL_RF_STATE_RX,
						  RAIL_RF_STATE_RX },
			       .txTransitions = { RAIL_RF_STATE_RX,
						  RAIL_RF_STATE_RX } },
		.timings = { .idleToRx = 100,
			     .idleToTx = 100,
			     .rxToTx = 192,
			     .txToRx = 192 },
		.framesMask = RAIL_IEEE802154_ACCEPT_STANDARD_FRAMES,
		.promiscuousMode = true,
	};
	RAIL_TxPowerConfig_t power = { .mode = RAIL_TX_POWER_MODE_2P4GIG_HP,
				       .voltage = 3300,
				       .rampTime = 10 };
	RAIL_StateTransitions_t transitions = { RAIL_RF_STATE_RX,
						RAIL_RF_STATE_RX };
	uint16_t rx_size = sizeof(rx_fifo);

#ifdef EC_RF_REJECT_DIAGNOSTICS
	if (channel != 11)
		return -1;
#else
	if (channel != 11 && channel != 26)
		return -1;
#endif
	selected_channel = 0;
	/* Core only calls start while inactive; failed attempts also reset counts. */
	ec_mg24_health_reset();
#ifdef EC_RF_REJECT_DIAGNOSTICS
	ec_mg24_reject_reset();
#endif
	if (radio) {
		if (RAIL_StartRx(radio, channel, NULL) != RAIL_STATUS_NO_ERROR)
			return -1;
		selected_channel = channel;
		return 0;
	}
	/* XIAO antenna switch: PB4 selects onboard antenna; PB5 enables switch. */
	GPIO_PinModeSet(gpioPortB, 4, gpioModePushPull, 0);
	GPIO_PinModeSet(gpioPortB, 5, gpioModePushPull, 1);
	radio = RAIL_Init(&config, NULL);
	if (!radio)
		return -1;
	if (RAIL_IEEE802154_Config2p4GHzRadio(radio) ||
	    RAIL_IEEE802154_Init(radio, &ieee) ||
	    RAIL_ConfigRxOptions(radio, RAIL_RX_OPTION_STORE_CRC,
				 RAIL_RX_OPTION_STORE_CRC) ||
#ifdef EC_RF_REJECT_DIAGNOSTICS
	    RAIL_ConfigRxOptions(radio, RAIL_RX_OPTION_IGNORE_CRC_ERRORS,
				 RAIL_RX_OPTION_IGNORE_CRC_ERRORS) ||
#endif
	    RAIL_SetRxFifo(radio, rx_fifo, &rx_size) ||
#ifndef EC_RF_REJECT_DIAGNOSTICS
	    RAIL_SetTxFifo(radio, tx_fifo, 0, sizeof(tx_fifo)) !=
		    sizeof(tx_fifo) ||
#endif
	    RAIL_InitTxPowerCurvesAlt(&RAIL_TxPowerCurvesVbat) ||
	    RAIL_ConfigTxPower(radio, &power) || RAIL_SetTxPowerDbm(radio, 0) ||
	    RAIL_SetRxTransitions(radio, &transitions) ||
	    RAIL_SetTxTransitions(radio, &transitions) ||
	    RAIL_ConfigCal(radio, RAIL_CAL_ALL) ||
	    RAIL_ConfigEvents(radio, RAIL_EVENTS_ALL,
			      RAIL_EVENT_RX_PACKET_RECEIVED |
				      RAIL_EVENT_RX_FIFO_OVERFLOW |
				      RAIL_EVENT_RX_FRAME_ERROR |
				      RAIL_EVENTS_TX_COMPLETION |
				      RAIL_EVENT_CAL_NEEDED))
		return -1;
	if (RAIL_StartRx(radio, channel, NULL) != RAIL_STATUS_NO_ERROR)
		return -1;
	selected_channel = channel;
	return 0;
}

/** @brief Copy one synthetic packet into RAIL-owned FIFO and submit once.
 * @param mac MAC bytes, excluding the hardware-generated IEEE FCS.
 * @param length MAC byte count, at most 125.
 * @param cca Nonzero selects RAIL's single-CCA configuration, with no retries.
 * @return Zero on submission; nonzero after aborting and restoring RX.
 */
static int send(const uint8_t *mac, size_t length, unsigned cca)
{
#ifdef EC_RF_REJECT_DIAGNOSTICS
	(void)mac;
	(void)length;
	(void)cca;
	return -1;
#else
	uint8_t packet[126];
	RAIL_CsmaConfig_t csma = RAIL_CSMA_CONFIG_SINGLE_CCA;
	RAIL_Status_t status;

	if (!radio || selected_channel != 26 || !length || length > 125)
		return -1;
	packet[0] = length + 2;
	memcpy(packet + 1, mac, length);
	RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
	if (RAIL_WriteTxFifo(radio, packet, length + 1, true) != length + 1) {
		RAIL_StartRx(radio, 26, NULL);
		return -1;
	}
	status = cca ? RAIL_StartCcaCsmaTx(radio, 26, RAIL_TX_OPTIONS_DEFAULT,
					   &csma, NULL)
		     : RAIL_StartTx(radio, 26, RAIL_TX_OPTIONS_DEFAULT, NULL);
	if (status != RAIL_STATUS_NO_ERROR) {
		RAIL_Idle(radio, RAIL_IDLE_ABORT, true);
		RAIL_StartRx(radio, 26, NULL);
		return -1;
	}
	return 0;
#endif
}

/** @brief Drain at most four held frames per pass, releasing every packet handle. */
static void poll(void)
{
	if (!radio)
		return;
	for (unsigned i = 0; i < 4; i++) {
		RAIL_RxPacketInfo_t info;
		RAIL_RxPacketDetails_t details;
		RAIL_RxPacketHandle_t packet;
		enum ec_mg24_rx_reason reason;
		uint8_t bytes[128];
		uint32_t ticks = RAIL_GetTime();

		packet = RAIL_GetRxPacketInfo(
			radio, RAIL_RX_PACKET_HANDLE_OLDEST_COMPLETE, &info);
		if (packet == RAIL_RX_PACKET_HANDLE_INVALID)
			break;
#ifdef EC_RF_REJECT_DIAGNOSTICS
		if (info.packetStatus == RAIL_RX_PACKET_READY_CRC_ERROR) {
			struct ec_mg24_reject_metadata metadata = {
				.status = info.packetStatus,
				.packet_bytes = info.packetBytes,
				.ticks = ticks,
			};
			uint16_t wanted = info.packetBytes > sizeof(bytes)
						  ? sizeof(bytes)
						  : info.packetBytes;
			uint16_t copied;

			metadata.details_available =
				RAIL_GetRxPacketDetailsAlt(radio, packet,
							   &details) ==
				RAIL_STATUS_NO_ERROR;
			if (metadata.details_available) {
				metadata.crc_passed = details.crcPassed;
				metadata.rssi = details.rssi;
				metadata.lqi = details.lqi;
			}
			copied = RAIL_PeekRxPacket(radio, packet, bytes, wanted,
						   0);
			RAIL_ReleaseRxPacket(radio, packet);
			ec_mg24_reject_record(&metadata, bytes, copied);
			ec_rf_drop();
			continue;
		}
#endif
		if (info.packetStatus != RAIL_RX_PACKET_READY_SUCCESS)
			reason = EC_MG24_PACKET_STATUS_INVALID;
		else if (info.packetBytes < 4 ||
			 info.packetBytes > sizeof(bytes))
			reason = EC_MG24_PACKET_LENGTH_INVALID;
		else if (RAIL_GetRxPacketDetailsAlt(radio, packet, &details) !=
			 RAIL_STATUS_NO_ERROR)
			reason = EC_MG24_PACKET_DETAILS_FAILED;
		else
			reason = EC_MG24_REASON_COUNT;
		if (reason == EC_MG24_REASON_COUNT) {
			RAIL_CopyRxPacket(bytes, &info);
			RAIL_ReleaseRxPacket(radio, packet);
			if (!(bytes[0] & 0x80) &&
			    bytes[0] + 1 == info.packetBytes)
				ec_rf_receive(bytes + 1, bytes[0] - 2, 1,
					      details.rssi, details.lqi, ticks);
			else {
				ec_mg24_health_count(
					EC_MG24_PACKET_LAYOUT_INVALID);
				ec_rf_drop();
			}
		} else {
			RAIL_ReleaseRxPacket(radio, packet);
			ec_mg24_health_count(reason);
			ec_rf_drop();
		}
	}
	if (atomic_exchange(&calibration, 0))
		if (RAIL_Calibrate(radio, NULL, RAIL_CAL_ALL_PENDING)) {
			ec_mg24_health_count(EC_MG24_CALIBRATION_FAILED);
			ec_rf_drop();
		}
}

/** @brief Return the configured power, conservatively rounding positive values up.
 * @return Configured dBm, or zero before initialization.
 */
static int power_dbm(void)
{
	int power = radio ? RAIL_GetTxPowerDbm(radio) : 0;

	return power > 0 ? (power + 9) / 10 : power / 10;
}

const struct ec_rf_ops ec_mg24_rf_ops = {
	now_us, start, stop, send, poll, power_dbm, ec_mg24_health,
};
