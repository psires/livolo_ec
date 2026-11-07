/* SPDX-License-Identifier: BSD-2-Clause */
#include "transport.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/** @brief Run only bounded USB diagnostics; never initialize a radio stack. */
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
	if (usb_serial_jtag_driver_install(&config) != ESP_OK)
		return;
	ec_experimental_init(&parser, "seeed-xiao-esp32c6", "xiao-bringup-1",
			     115200);
	for (;;) {
		if (sent < pending) {
			int written = usb_serial_jtag_write_bytes(
				response + sent, pending - sent, 0);

			if (written > 0)
				sent += written;
			vTaskDelay(1);
			continue;
		}
		if (consumed >= count) {
			count = usb_serial_jtag_read_bytes(input, sizeof(input),
							   pdMS_TO_TICKS(10));
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
	}
}
