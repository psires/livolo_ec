/* SPDX-License-Identifier: BSD-2-Clause */
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "health.h"
#include "rf.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

/** @brief Exercise the actual RF-enabled application loop using host-only drivers. */
void app_main(void);

/** @brief Inject a synthetic watchdog observation through the actual ISR hook. */
void esp_task_wdt_isr_user_handler(void);

static jmp_buf finished;
static uint32_t clock_us;
static unsigned reads, writes, starts, stops, sends, injected;
enum scenario_kind { NORMAL, STALLED, PASSIVE, HEALTH };
static enum scenario_kind kind;
static size_t input_used, output_used;
static char output[1024];
static const char *input, *expected;
static const char synthetic_input[] =
	"1 RFSTART 1234abcd\n2 RFTX 1234abcd 7 39 0 0\n";
static const char synthetic_expected[] = "RSP 1 OK active=1 channel=26\n"
					 "RSP 2 OK queued=1 seq=7\n";
static const char passive_input[] =
	"1 RFLISTEN 1234abcd\n2 RFTX 1234abcd 7 39 0 0\n";
static const char passive_expected[] =
	"RSP 1 OK active=1 channel=11 mode=passive\n"
	"RSP 2 ERR RF_PASSIVE\n";
static const char tx_event[] = "RF TX seq=7 status=done\n";
static const char rx_event[] = "RF RX len=3 fcs=stripped crc=1 rssi=-40 "
			       "lqi=100 ticks=123 data=018800\n";

/** @brief Supply the application-controlled mock clock.
 * @return Time in microseconds modulo 2^32.
 */
static uint32_t now_us(void)
{
	return clock_us;
}

/** @brief Admit only the fixed channel expected by the current mock scenario.
 * @param channel Synthetic channel 26 or passive channel 11.
 * @return Zero for success.
 */
static int start(unsigned channel)
{
	assert(channel == (kind == PASSIVE ? 11u : 26u));
	starts++;
	return 0;
}

/** @brief Record cancellation independently of any USB progress.
 * @return Zero confirms mock radio quiescence.
 */
static int stop(void)
{
	stops++;
	return 0;
}

/** @brief Admit one delayed transmission so the real loop must service callbacks.
 * @param mac Borrowed synthetic MAC bytes.
 * @param length MAC byte count excluding FCS.
 * @param cca Zero for this immediate-transmission fixture.
 * @return Zero after accepting one asynchronous request.
 */
static int send_frame(const uint8_t *mac, size_t length, unsigned cca)
{
	assert(length == 39 && !cca);
	assert(mac[0] == 1 && mac[1] == 0x88 && mac[2] == 7);
	assert(++sends == 1);
	return 0;
}

/** @brief Leave callback scheduling under the synthetic serial driver's control. */
static void poll(void)
{
}

/** @brief Report the bounded synthetic power setting.
 * @return Zero dBm.
 */
static int power_dbm(void)
{
	return 0;
}

const struct ec_rf_ops ec_c6_rf_ops = {
	.now_us = now_us,
	.start = start,
	.stop = stop,
	.send = send_frame,
	.poll = poll,
	.power_dbm = power_dbm,
	.health = ec_c6_health,
};

esp_reset_reason_t esp_reset_reason(void)
{
	return 7;
}

int64_t esp_timer_get_time(void)
{
	return clock_us;
}

unsigned uxTaskGetStackHighWaterMark(void *task)
{
	assert(!task);
	return 2048;
}

int usb_serial_jtag_driver_install(
	const usb_serial_jtag_driver_config_t *config)
{
	assert(config->tx_buffer_size == 2048 &&
	       config->rx_buffer_size == 2048);
	if (kind == HEALTH)
		esp_task_wdt_isr_user_handler();
	return ESP_OK;
}

void esp_log_level_set(const char *tag, int level)
{
	assert(!strcmp(tag, "*") && level == ESP_LOG_NONE);
}

void vTaskDelay(uint32_t ticks)
{
	clock_us += ticks * 1000;
	assert(clock_us < 1000000);
	if (kind == STALLED && stops) {
		assert(starts == 1 && sends == 1);
		assert(clock_us < 100000);
		longjmp(finished, 1);
	}
}

int usb_serial_jtag_read_bytes(void *buffer, size_t length, uint32_t ticks)
{
	static const uint8_t mac[] = { 0x01, 0x88, 0x00 };
	size_t remaining = strlen(input) - input_used;

	assert(++reads < 100);
	if (remaining) {
		if (kind == HEALTH) {
			esp_task_wdt_isr_user_handler();
			esp_task_wdt_isr_user_handler();
		}
		assert(remaining <= length);
		memcpy(buffer, input + input_used, remaining);
		input_used += remaining;
		return (int)remaining;
	}
	if (kind == HEALTH) {
		assert(!starts && !stops && !sends);
		assert(strstr(output, "RSP 1 OK "));
		assert(strstr(output, "reset_reason=7 "));
		assert(strstr(output, "read_calls=1 "));
		assert(strstr(output, "read_bytes=11 "));
		assert(strstr(output, "mainstack_min_bytes=2048 "));
		assert(strstr(output, "task_wdt_events=3"));
		assert(strstr(output, "counter_bits=32"));
		assert(output[output_used - 1] == '\n');
		longjmp(finished, 1);
	}
	if (!injected) {
		assert(kind != STALLED);
		assert(sends == (kind == PASSIVE ? 0u : 1u));
		if (kind != PASSIVE)
			ec_rf_tx_complete(1);
		ec_rf_receive(mac, sizeof(mac), 0, -40, 100, 123);
		injected = 1;
	}
	if (output_used == strlen(expected) + strlen(rx_event) +
				   (kind == PASSIVE ? 0 : strlen(tx_event))) {
		assert(!memcmp(output, expected, strlen(expected)));
		assert(strstr(output, rx_event));
		if (kind == PASSIVE)
			assert(!strstr(output, "RF TX ") && !sends);
		else
			assert(strstr(output, tx_event));
		assert(starts == 1 && !stops);
		longjmp(finished, 1);
	}
	clock_us += ticks * 1000;
	return 0;
}

int usb_serial_jtag_write_bytes(const void *buffer, size_t length,
				uint32_t ticks)
{
	size_t count = length > 7 ? 7 : length;

	assert(ticks == 0 && length);
	assert(++writes < 1000);
	if (kind == STALLED && sends)
		return 0;
	assert(output_used + count < sizeof(output));
	memcpy(output + output_used, buffer, count);
	output_used += count;
	output[output_used] = '\0';
	return (int)count;
}

/** @brief Run each real-loop scenario with independent synthetic boot state.
 * @param selected Select normal TX, stalled output, passive RX or RF-off health.
 */
static void scenario(enum scenario_kind selected)
{
	clock_us = reads = writes = starts = stops = sends = injected = 0;
	input_used = output_used = 0;
	kind = selected;
	input = kind == HEALTH	  ? "1 RFHEALTH\n"
		: kind == PASSIVE ? passive_input
				  : synthetic_input;
	expected = kind == PASSIVE ? passive_expected : synthetic_expected;
	output[0] = '\0';
	if (!setjmp(finished)) {
		app_main();
		assert(!"application unexpectedly returned");
	}
}

/** @brief Distinguish USB errors, empty progress and pending output without RF. */
static void test_health_evidence(void)
{
	char report[640];
	size_t length;

	starts = stops = sends = 0;
	clock_us = 1234000;
	ec_c6_health_init();
	ec_rf_setup(&ec_c6_rf_ops);
	ec_c6_health_loop(29);
	ec_c6_health_read(-1);
	ec_c6_health_read(0);
	ec_c6_health_read(7);
	ec_c6_health_write(-1);
	ec_c6_health_write(0);
	ec_c6_health_write(3);
	esp_task_wdt_isr_user_handler();
	assert(!ec_rf_command("RFHEALTH", report, 8));
	length = ec_rf_command("RFHEALTH", report, sizeof(report));
	assert(length && length == strlen(report));
	assert(strstr(report, "uptime_ms=1234 reset_reason=7 loops=1 "));
	assert(strstr(report,
		      "read_calls=3 read_bytes=7 read_zero=1 read_error=1 "));
	assert(strstr(
		report,
		"write_calls=3 written_bytes=3 write_zero=1 write_error=1 "));
	assert(strstr(
		report,
		"pending=29 mainstack_min_bytes=2048 task_wdt_events=1 "));
	assert(!starts && !stops && !sends);
	/* Health reads must preserve accumulated evidence, including ISR reports. */
	assert(ec_rf_command("RFHEALTH", report, sizeof(report)) == length);
	assert(strstr(report, "task_wdt_events=1 "));
}

/** @brief Verify RF bounds and diagnostic evidence through the actual USB loop.
 * @return Zero after synthetic, passive, stalled-output and health checks pass.
 */
int main(void)
{
	scenario(NORMAL);
	scenario(STALLED);
	scenario(PASSIVE);
	scenario(HEALTH);
	test_health_evidence();
	puts("ESP32-C6 RF events, passive RX, health and blocked USB: PASS");
	return 0;
}
