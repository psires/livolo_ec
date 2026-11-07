/* SPDX-License-Identifier: BSD-2-Clause */
#include "transport.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#ifdef EC_RF_DIAGNOSTICS
#include "rf.h"
#include "health.h"
extern const struct ec_rf_ops ec_c6_rf_ops;
#endif

/** @brief Run diagnostics; only RFSTART or RFLISTEN can enable the raw radio. */
void app_main(void)
{
	usb_serial_jtag_driver_config_t config = {
		.tx_buffer_size = 2048,
		.rx_buffer_size = 2048,
	};
	struct ec_experimental parser;
	char response[640];
	uint8_t input[64];
	size_t pending = 0, sent = 0;
	int count = 0, consumed = 0;

	esp_log_level_set("*", ESP_LOG_NONE);
#ifdef EC_RF_DIAGNOSTICS
	ec_c6_health_init();
#endif
	if (usb_serial_jtag_driver_install(&config) != ESP_OK)
		return;
	ec_experimental_init(&parser, "seeed-xiao-esp32c6", "xiao-bringup-1",
			     115200);
#ifdef EC_RF_DIAGNOSTICS
	ec_rf_setup(&ec_c6_rf_ops);
	parser.radio_command = ec_rf_command;
	parser.build = "xiao-rx-1";
#endif
	for (;;) {
#ifdef EC_RF_DIAGNOSTICS
		ec_c6_health_loop(pending - sent);
		/* Single task owns core state; expiry progresses even with blocked USB. */
		ec_rf_poll();
#endif
		if (sent < pending) {
			int written = usb_serial_jtag_write_bytes(
				response + sent, pending - sent, 0);

#ifdef EC_RF_DIAGNOSTICS
			ec_c6_health_write(written);
#endif
			if (written > 0)
				sent += written;
			vTaskDelay(1);
			continue;
		}
		pending = sent = 0;
		if (consumed >= count) {
			count = usb_serial_jtag_read_bytes(input, sizeof(input),
							   pdMS_TO_TICKS(10));
#ifdef EC_RF_DIAGNOSTICS
			ec_c6_health_read(count);
#endif
			consumed = 0;
			if (count < 0) {
				count = 0;
				vTaskDelay(1);
			}
		}
		if (count > consumed) {
			pending = ec_experimental_byte(
				&parser, input[consumed++], response,
				sizeof(response));
			sent = 0;
		}
#ifdef EC_RF_DIAGNOSTICS
		if (!pending) {
			pending = ec_rf_event(response, sizeof(response));
			sent = 0;
		}
#endif
	}
}
