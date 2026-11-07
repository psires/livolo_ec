/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_EXPERIMENTAL_RF_H
#define EC_EXPERIMENTAL_RF_H
#include <stddef.h>
#include <stdint.h>
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#define EC_RF_ISR_ATTR IRAM_ATTR
#else
#define EC_RF_ISR_ATTR
#endif

/* Board operations run only from the main task, never from USB write callbacks. */
struct ec_rf_ops {
	/* Monotonic microseconds modulo 2^32; callable from the owning main task. */
	uint32_t (*now_us)(void);
	/* Start fixed channel-26 RX/no-ACK at <=0dBm; zero succeeds, nonzero fails. */
	int (*start)(void);
	/* Cancel hardware and quiesce callbacks; nonzero permanently fences restart. */
	int (*stop)(void);
	/* Borrow mac[length] through terminal completion; cca is zero/one.
	 * Zero admits one attempt. Nonzero guarantees no later use of caller bytes.
	 */
	int (*send)(const uint8_t *mac, size_t length, unsigned cca);
	/* Bounded deferred SDK work; must never wait for serial output. */
	void (*poll)(void);
	/* Read configured dBm (not measured RF power); positive values reject start. */
	int (*power_dbm)(void);
};

/** @brief Install board operations without initializing or enabling the radio.
 * @param ops Static board adapter; callbacks remain valid for firmware lifetime.
 */
void ec_rf_setup(const struct ec_rf_ops *ops);
/** @brief Handle strictly bounded synthetic RF diagnostics, never arbitrary TX.
 * @param command Complete command after the numeric request ID.
 * @param out Response body storage, not including the UART RSP prefix.
 * @param capacity Available response bytes including NUL.
 * @return Response body length, or zero if storage is insufficient.
 */
size_t ec_rf_command(const char *command, char *out, size_t capacity);
/** @brief Service callbacks, expiry and timeout before every USB/UART iteration. */
void ec_rf_poll(void);
/** @brief Retrieve one complete event without blocking or partial consumption.
 * @param out Event output buffer, including newline.
 * @param capacity Buffer size; insufficient storage leaves the event queued.
 * @return Complete event length, or zero when no event fits.
 */
size_t ec_rf_event(char *out, size_t capacity);
/** @brief Copy a CRC-valid RX frame into the bounded ISR-to-main ring.
 * @param data MAC bytes plus FCS only when included is nonzero.
 * @param length MAC length excluding two FCS bytes; maximum 125.
 * @param included Nonzero when two original FCS bytes follow the MAC bytes.
 * @param rssi Received signal strength in dBm.
 * @param lqi Link quality supplied by the radio.
 * @param ticks Callback timestamp in microseconds, modulo 2^32.
 */
void EC_RF_ISR_ATTR ec_rf_receive(const uint8_t *data, size_t length,
				  unsigned included, int rssi, unsigned lqi,
				  uint32_t ticks);
/** @brief Count one hardware/malformed receive discard without exposing bytes. */
void EC_RF_ISR_ATTR ec_rf_drop(void);
/** @brief Publish the terminal callback for the currently owned TX buffer.
 * @param success Nonzero only on actual hardware transmission completion.
 */
void EC_RF_ISR_ATTR ec_rf_tx_complete(unsigned success);

#endif
