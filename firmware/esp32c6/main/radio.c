/* SPDX-License-Identifier: BSD-2-Clause */
#include "rf.h"
#include "health.h"
#include "adapter.h"
#include "driver/gpio.h"
#include "esp_ieee802154.h"
#include "esp_timer.h"
#include <string.h>

static int initialized;
static unsigned selected_channel;
static uint8_t tx[128];

/** @brief Read microseconds modulo 2^32 from the monotonic timer.
 * @return Callback/poll timestamp; this is not an RF SFD timestamp.
 */
static uint32_t IRAM_ATTR now_us(void)
{
	return (uint32_t)esp_timer_get_time();
}

/** @brief Stop RX/TX synchronously without changing USB or persistent storage.
 * @return Zero only after the SDK confirms sleep; failure fences restart.
 */
static int stop(void)
{
	selected_channel = 0;
	if (initialized) {
		int result = esp_ieee802154_set_rx_when_idle(false);

		/* Pinned IDF sleep enters a critical section and stops current operation. */
		result |= esp_ieee802154_sleep();
		return result || esp_ieee802154_get_state() !=
					       ESP_IEEE802154_RADIO_SLEEP
			       ? -1
			       : 0;
	}
	return 0;
}

/** @brief Start promiscuous fixed-channel RX with hardware ACKs disabled.
 * @param channel Fixed passive channel 11 or synthetic channel 26.
 * @return Zero on verified setup; nonzero leaves the portable core fail-closed.
 */
static int start(unsigned channel)
{
	if (channel != 11 && channel != 26)
		return -1;
	ec_c6_adapter_reset();
	selected_channel = 0;
	if (!initialized) {
		gpio_config_t pins = {
			.pin_bit_mask = (1ULL << 3) | (1ULL << 14),
			.mode = GPIO_MODE_OUTPUT,
		};

		if (gpio_config(&pins) != ESP_OK ||
		    gpio_set_level(3, 0) != ESP_OK ||
		    gpio_set_level(14, 0) != ESP_OK)
			return -1;
		if (esp_ieee802154_enable() != ESP_OK)
			return -1;
		initialized = 1;
	}
	/* IDF v5.5.1 sets auto_ack_rx, auto_ack_tx and enhance_ack_tx false here. */
	if (esp_ieee802154_set_promiscuous(true) != ESP_OK ||
	    esp_ieee802154_set_channel(channel) != ESP_OK ||
	    esp_ieee802154_set_txpower(0) != ESP_OK ||
	    esp_ieee802154_set_rx_when_idle(true) != ESP_OK ||
	    !esp_ieee802154_get_promiscuous() ||
	    esp_ieee802154_get_channel() != channel ||
	    esp_ieee802154_get_txpower() > 0)
		return -1;
	if (esp_ieee802154_receive() != ESP_OK)
		return -1;
	selected_channel = channel;
	return 0;
}

/** @brief Submit one owned frame; hardware supplies IEEE FCS and optional CCA.
 * @param mac Synthetic MAC bytes, excluding FCS.
 * @param length MAC byte count, at most 125.
 * @param cca Nonzero requests a single SDK CCA transmission attempt.
 * @return Zero on admission, otherwise nonzero with no borrowed caller storage.
 */
static int send(const uint8_t *mac, size_t length, unsigned cca)
{
	if (!initialized || selected_channel != 26 || !length || length > 125)
		return -1;
	tx[0] = length + 2;
	memcpy(tx + 1, mac, length);
	tx[length + 1] = tx[length + 2] = 0;
	return esp_ieee802154_transmit(tx, cca != 0) == ESP_OK ? 0 : -1;
}

/** @brief No deferred SDK work is required; RX callbacks copy and release. */
static void poll(void)
{
}

/** @brief Report configured power, not a calibrated radiated-power measurement.
 * @return SDK power setting, or zero while the radio is uninitialized.
 */
static int power_dbm(void)
{
	return initialized ? esp_ieee802154_get_txpower() : 0;
}

const struct ec_rf_ops ec_c6_rf_ops = {
	now_us, start, stop, send, poll, power_dbm, ec_c6_health, ec_c6_adapter,
};

/** @brief Copy valid MAC bytes, then always release the driver's RX buffer.
 * @param frame SDK-owned frame: PHR then MAC then substituted RSSI/LQI bytes.
 * @param info SDK RX metadata; hardware has accepted the frame FCS.
 */
void IRAM_ATTR esp_ieee802154_receive_done(uint8_t *frame,
					   esp_ieee802154_frame_info_t *info)
{
	ec_c6_adapter_count(EC_C6_ADAPTER_RX_DONE);
	if (frame && info && frame[0] >= 3 && frame[0] <= 127) {
		ec_c6_adapter_count(EC_C6_ADAPTER_CORE_CALLS);
		ec_rf_receive(frame + 1, frame[0] - 2, 0, info->rssi, info->lqi,
			      now_us());
	} else {
		ec_c6_adapter_count(EC_C6_ADAPTER_RX_INVALID);
		if (!frame)
			ec_c6_adapter_count(EC_C6_ADAPTER_RX_NULL);
		ec_rf_drop();
	}
	if (frame)
		ec_c6_adapter_count(esp_ieee802154_receive_handle_done(frame) ==
						    ESP_OK
					    ? EC_C6_ADAPTER_RX_RELEASE_OK
					    : EC_C6_ADAPTER_RX_RELEASE_FAIL);
}

/** @brief Count the SDK's already-enabled SFD indications in ISR context.
 * This is not a unique-packet count or an identification of a CRC failure.
 */
void IRAM_ATTR esp_ieee802154_receive_sfd_done(void)
{
	ec_c6_adapter_count(EC_C6_ADAPTER_SFD_EVENT);
}

/** @brief Publish TX completion separately from any independent air reception.
 * @param frame Owned TX frame; retained until this callback.
 * @param ack Optional SDK-owned ACK, always released if unexpectedly supplied.
 * @param info Optional ACK metadata, unused by the diagnostic core.
 */
void IRAM_ATTR esp_ieee802154_transmit_done(const uint8_t *frame,
					    const uint8_t *ack,
					    esp_ieee802154_frame_info_t *info)
{
	(void)frame;
	(void)info;
	if (ack)
		ec_c6_adapter_count(esp_ieee802154_receive_handle_done(ack) ==
						    ESP_OK
					    ? EC_C6_ADAPTER_ACK_RELEASE_OK
					    : EC_C6_ADAPTER_ACK_RELEASE_FAIL);
	ec_rf_tx_complete(1);
}

/** @brief Publish a failed one-shot attempt; no retry is scheduled.
 * @param frame Owned TX storage, retained until this callback.
 * @param error SDK terminal failure code, currently counted as one failure.
 */
void IRAM_ATTR esp_ieee802154_transmit_failed(const uint8_t *frame,
					      esp_ieee802154_tx_error_t error)
{
	(void)frame;
	(void)error;
	ec_rf_tx_complete(0);
}
