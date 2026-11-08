/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef TEST_ADAPTER_IEEE_H
#define TEST_ADAPTER_IEEE_H
#include <stdbool.h>
#include <stdint.h>
#define IRAM_ATTR
#define ESP_OK 0
#define ESP_IEEE802154_RADIO_SLEEP 0
typedef struct {
	int rssi;
	unsigned lqi;
} esp_ieee802154_frame_info_t;
typedef unsigned esp_ieee802154_tx_error_t;
#define esp_ieee802154_set_rx_when_idle(v) ((void)(v), 0)
#define esp_ieee802154_sleep() 0
#define esp_ieee802154_get_state() 0
#define esp_ieee802154_enable() 0
#define esp_ieee802154_set_promiscuous(v) ((void)(v), 0)
#define esp_ieee802154_set_channel(v) (test_channel = (v), 0)
#define esp_ieee802154_set_txpower(v) ((void)(v), 0)
#define esp_ieee802154_get_promiscuous() true
#define esp_ieee802154_get_channel() test_channel
#define esp_ieee802154_get_txpower() 0
#define esp_ieee802154_receive() test_start_result
#define esp_ieee802154_transmit(f, c) ((void)(f), (void)(c), 0)
#define esp_ieee802154_receive_handle_done(f)                                  \
	((void)(f), test_releases++, test_release_result)
#endif
