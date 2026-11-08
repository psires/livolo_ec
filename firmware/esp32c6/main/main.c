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

/** @brief Give idle/watchdog work a tick after bounded productive or blocked work.
 * @param input_budget Input bytes consumed since the last explicit yield.
 * @param output_bytes Output bytes accepted since the last explicit yield.
 * @param output_calls Nonblocking write attempts since the last explicit yield.
 * @param output_records Complete output records since the last explicit yield.
 */
static void yield_work(unsigned *input_budget, unsigned *output_bytes,
		       unsigned *output_calls, unsigned *output_records)
{
	vTaskDelay(1);
	*input_budget = 0;
	*output_bytes = 0;
	*output_calls = 0;
	*output_records = 0;
}

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
	unsigned input_budget = 0;
	unsigned output_bytes = 0, output_calls = 0, output_records = 0;
	int count = 0, consumed = 0;
#ifdef EC_RF_DIAGNOSTICS
	unsigned prefer_rf = 0;
#endif

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
	parser.build = "xiao-rxdiag-1";
	parser.adapter_diagnostics = 1;
#endif
	for (;;) {
#ifdef EC_RF_DIAGNOSTICS
		ec_c6_health_loop(pending - sent);
		/* Single task owns core state; expiry progresses even with blocked USB. */
		ec_rf_poll();
#endif
		if (sent < pending) {
			size_t offered = pending - sent;
			unsigned remaining_budget = 1024 - output_bytes;
			int written;

			if (offered > remaining_budget)
				offered = remaining_budget;
			written = usb_serial_jtag_write_bytes(response + sent,
							      offered, 0);

#ifdef EC_RF_DIAGNOSTICS
			ec_c6_health_write(written);
#endif
			output_calls++;
			if (written > 0) {
				sent += written;
				output_bytes += written;
				if (sent == pending)
					output_records++;
			}
			/* Preserve the suffix while allowing a bounded successful burst. */
			if (written <= 0 || output_bytes >= 1024 ||
			    output_calls >= 16 || output_records >= 4)
				yield_work(&input_budget, &output_bytes,
					   &output_calls, &output_records);
			continue;
		}
		pending = sent = 0;
#ifdef EC_RF_DIAGNOSTICS
		/* A stream of short command replies must not starve ready RF output. */
		if (prefer_rf) {
			pending = ec_rf_event(response, sizeof(response));
			if (pending) {
				prefer_rf = 0;
				continue;
			}
		}
#endif
		if (consumed >= count) {
			uint32_t wait_ticks = pdMS_TO_TICKS(10);

#ifdef EC_RF_DIAGNOSTICS
			/* Advisory hint: a later IRQ can still race the idle wait. */
			if (ec_rf_events_pending())
				wait_ticks = 0;
#endif
			count = usb_serial_jtag_read_bytes(input, sizeof(input),
							   wait_ticks);
#ifdef EC_RF_DIAGNOSTICS
			ec_c6_health_read(count);
#endif
			consumed = 0;
			if (count < 0) {
				count = 0;
				yield_work(&input_budget, &output_bytes,
					   &output_calls, &output_records);
			}
		}
		/* Commands get bounded progress between complete output records. */
		for (unsigned steps = 0;
		     steps < 16 && count > consumed && !pending; steps++) {
			pending = ec_experimental_byte(
				&parser, input[consumed++], response,
				sizeof(response));
#ifdef EC_RF_DIAGNOSTICS
			if (pending)
				prefer_rf = 1;
#endif
			if (++input_budget == 256) {
				yield_work(&input_budget, &output_bytes,
					   &output_calls, &output_records);
				break;
			}
		}
#ifdef EC_RF_DIAGNOSTICS
		if (!pending) {
			pending = ec_rf_event(response, sizeof(response));
			sent = 0;
			if (pending)
				prefer_rf = 0;
		}
#endif
	}
}
