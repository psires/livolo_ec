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

/** @brief Execute the actual application with deterministic USB and radio drivers. */
void app_main(void);

enum scenario {
	BURST,
	PARTIAL,
	BACKPRESSURE,
	READ_ERROR,
	CONTINUOUS,
	MIXED,
	LEASE_BLOCKED,
	IDLE
};
static enum scenario kind;
static jmp_buf finished;
static uint32_t clock_us;
static unsigned starts, stops, injected, rx_lines, ping_lines, yields;
static unsigned attempts, bytes_since_yield, calls_since_yield;
static unsigned records_since_yield, pings_since_rf, read_calls;
static unsigned reply_done, stop_reply, must_yield, read_error_done;
static size_t input_offset, line_used;
static char commands[512], line[640], expected_rx[640];
static uint8_t packet[125];

/** @brief Supply microseconds without wall-clock waiting.
 * @return Simulated monotonic radio time modulo 2^32.
 */
static uint32_t now_us(void)
{
	return clock_us;
}

/** @brief Open only the explicit passive fixture window.
 * @param channel Must be channel11, never a synthetic transmission.
 * @return Zero after recording successful admission.
 */
static int start(unsigned channel)
{
	assert(channel == 11);
	assert(++starts == 1);
	return 0;
}

/** @brief Record stop/lease expiry without clearing queued records.
 * @return Zero confirms radio quiescence in the fixture.
 */
static int stop(void)
{
	stops++;
	return 0;
}

/** @brief Refuse any unexpected on-air transmission in these receive-only tests.
 * @param mac Borrowed synthetic payload, unused because transmission is forbidden.
 * @param length Payload length, unused.
 * @param cca Carrier-assessment setting, unused.
 * @return Never returns: unexpected transmission fails the fixture.
 */
static int send_frame(const uint8_t *mac, size_t length, unsigned cca)
{
	(void)mac;
	(void)length;
	(void)cca;
	assert(!"receive-only scheduler fixture transmitted");
	return -1;
}

/** @brief Keep at most seven outstanding records, with controlled continuous arrivals. */
static void poll(void)
{
	unsigned limit = kind == CONTINUOUS || kind == MIXED ? 64 : 7;

	if (!reply_done || stops || kind == IDLE)
		return;
	while (injected < limit && injected - rx_lines < 7) {
		ec_rf_receive(packet, sizeof(packet), 0, -40, 100, 123);
		injected++;
	}
}

/** @brief Supply an unchanged bounded power setting.
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

/** @brief Preserve a stable reset reason across the virtual run.
 * @return Fixed fixture reason, not a hardware reset observation.
 */
esp_reset_reason_t esp_reset_reason(void)
{
	return 7;
}

/** @brief Expose virtual uptime to the actual health implementation.
 * @return Monotonic microseconds.
 */
int64_t esp_timer_get_time(void)
{
	return clock_us;
}

/** @brief Supply a stable stack watermark without altering stack policy.
 * @param task Null selects the current task.
 * @return Fixed unused-stack byte count.
 */
unsigned uxTaskGetStackHighWaterMark(void *task)
{
	assert(!task);
	return 2048;
}

/** @brief Check that the application retains both2048-byte USB buffers.
 * @param config Actual driver configuration.
 * @return Success without touching hardware.
 */
int usb_serial_jtag_driver_install(
	const usb_serial_jtag_driver_config_t *config)
{
	assert(config->tx_buffer_size == 2048 &&
	       config->rx_buffer_size == 2048);
	return ESP_OK;
}

/** @brief Verify console logging policy remains unchanged.
 * @param tag Wildcard logger tag.
 * @param level Existing disabled logging level.
 */
void esp_log_level_set(const char *tag, int level)
{
	assert(!strcmp(tag, "*") && level == ESP_LOG_NONE);
}

/** @brief Check hard work budgets and advance the pinned100Hz scheduler by one tick.
 * @param ticks Must be one deliberately yielded tick.
 */
void vTaskDelay(uint32_t ticks)
{
	assert(ticks == 1);
	assert(bytes_since_yield <= 1024);
	assert(calls_since_yield <= 16);
	assert(records_since_yield <= 4);
	bytes_since_yield = calls_since_yield = records_since_yield = 0;
	must_yield = 0;
	yields++;
	clock_us += 10000;
	assert(clock_us <= 60020000);
	if (kind == LEASE_BLOCKED && stops) {
		assert(starts == 1 && stops == 1 && rx_lines == 0);
		assert(clock_us >= 60000000);
		longjmp(finished, 1);
	}
}

/** @brief Supply commands while enforcing nonblocking reads whenever RF is ready.
 * @param buffer Destination for at most length bytes.
 * @param length Application's64-byte input capacity.
 * @param ticks Timeout in 100 Hz ticks; zero is required when RF is ready.
 * @return Accepted command bytes, an injected error, or a simulated timeout.
 */
int usb_serial_jtag_read_bytes(void *buffer, size_t length, uint32_t ticks)
{
	size_t remaining = strlen(commands) - input_offset;
	size_t count;

	assert(!must_yield);
	assert(length == 64 && ++read_calls < 1000);
	if (ec_rf_events_pending())
		assert(ticks == 0);
	else
		assert(ticks == 1);
	if (kind == IDLE) {
		assert(!starts && !attempts && !yields);
		if (read_calls == 3)
			longjmp(finished, 1);
	}
	if (kind == READ_ERROR && reply_done && !read_error_done) {
		read_error_done = 1;
		must_yield = 1;
		return -1;
	}
	if (remaining) {
		/* Keep initial admission separate from the continuous reply stream. */
		if (!reply_done)
			count = strlen("1 RFLISTEN 1234abcd\n");
		else
			count = remaining > length ? length : remaining;
		assert(count <= length && count <= remaining);
		memcpy(buffer, commands + input_offset, count);
		input_offset += count;
		return (int)count;
	}
	if (kind != IDLE && kind != LEASE_BLOCKED && injected == rx_lines &&
	    injected && !ec_rf_events_pending()) {
		assert(!line_used && yields > 0);
		if (kind == MIXED)
			assert(ping_lines == 32 && stop_reply && stops == 1);
		else
			assert(rx_lines == (kind == CONTINUOUS ? 64u : 7u));
		longjmp(finished, 1);
	}
	clock_us += ticks * 10000;
	return 0;
}

/** @brief Validate complete output lines without accepting mixed or truncated records. */
static void check_line(void)
{
	unsigned ident;

	line[line_used] = 0;
	if (!strcmp(line, "RSP 1 OK active=1 channel=11 mode=passive\n")) {
		assert(!reply_done);
		reply_done = 1;
	} else if (!strncmp(line, "RF RX ", 6)) {
		assert(!strcmp(line, expected_rx));
		rx_lines++;
		pings_since_rf = 0;
		if (kind == MIXED && rx_lines == 16)
			assert(ping_lines >= 8);
	} else if (!strcmp(line, "RSP 99 OK active=0\n")) {
		stop_reply = 1;
	} else {
		char expected[64];

		assert(kind == MIXED);
		assert(sscanf(line, "RSP %u", &ident) == 1);
		assert(ident == ping_lines + 2);
		snprintf(expected, sizeof(expected), "RSP %u OK EC-GATEWAY 3\n",
			 ident);
		assert(!strcmp(line, expected));
		ping_lines++;
		pings_since_rf++;
		if (ec_rf_events_pending())
			assert(pings_since_rf <= 1);
	}
	line_used = 0;
	records_since_yield++;
	assert(records_since_yield <= 4);
}

/** @brief Model complete/partial/failed USB writes and verify hard fairness budgets.
 * @param buffer Unchanged next suffix of the application's pending line.
 * @param length Offered bytes, clamped to the remaining output budget.
 * @param ticks Must be zero, preserving nonblocking output.
 * @return Accepted prefix length, zero backpressure, or an injected negative error.
 */
int usb_serial_jtag_write_bytes(const void *buffer, size_t length,
				uint32_t ticks)
{
	const unsigned char *data = buffer;
	size_t count = length;

	assert(!ticks && length && !must_yield);
	assert(length <= 1024 - bytes_since_yield);
	assert(++attempts < 10000 && ++calls_since_yield <= 16);
	if ((kind == LEASE_BLOCKED && reply_done) ||
	    (kind == BACKPRESSURE && attempts % 11 == 0)) {
		must_yield = 1;
		return 0;
	}
	if (kind == BACKPRESSURE && attempts % 13 == 0) {
		must_yield = 1;
		return -1;
	}
	if ((kind == PARTIAL || kind == BACKPRESSURE) && count > 7)
		count = 7;
	bytes_since_yield += count;
	assert(bytes_since_yield <= 1024);
	for (size_t i = 0; i < count; i++) {
		assert(line_used + 1 < sizeof(line));
		line[line_used++] = data[i];
		if (data[i] == '\n')
			check_line();
	}
	return (int)count;
}

/** @brief Run one independent real-main scenario with fixed protocol/RF data.
 * @param selected Workload or failure condition to exercise.
 */
static void scenario(enum scenario selected)
{
	char hex[251];
	size_t used;

	kind = selected;
	clock_us = starts = stops = injected = rx_lines = ping_lines = yields =
		0;
	attempts = bytes_since_yield = calls_since_yield = 0;
	records_since_yield = pings_since_rf = read_calls = 0;
	reply_done = stop_reply = must_yield = read_error_done = 0;
	input_offset = line_used = 0;
	for (size_t i = 0; i < sizeof(packet); i++) {
		packet[i] = (uint8_t)i;
		snprintf(hex + 2 * i, 3, "%02x", packet[i]);
	}
	snprintf(expected_rx, sizeof(expected_rx),
		 "RF RX len=125 fcs=stripped crc=1 rssi=-40 lqi=100 ticks=123 "
		 "data=%s\n",
		 hex);
	strcpy(commands, kind == IDLE ? "" : "1 RFLISTEN 1234abcd\n");
	if (kind == MIXED) {
		used = strlen(commands);
		for (unsigned i = 0; i < 32; i++)
			used += snprintf(commands + used,
					 sizeof(commands) - used, "%u PING\n",
					 i + 2);
		strcat(commands, "99 RFSTOP\n");
	}
	if (!setjmp(finished)) {
		app_main();
		assert(!"application unexpectedly returned");
	}
}

/** @brief Verify queue drain, two-way fairness, output integrity and lease service.
 * @return Zero only when every actual-main scenario completes under hard bounds.
 */
int main(void)
{
	scenario(BURST);
	scenario(PARTIAL);
	scenario(BACKPRESSURE);
	scenario(READ_ERROR);
	scenario(CONTINUOUS);
	scenario(MIXED);
	scenario(LEASE_BLOCKED);
	scenario(IDLE);
	puts("C6 queued output, two-way fairness, backpressure and lease "
	     "expiry: PASS");
	return 0;
}
