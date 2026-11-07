/* SPDX-License-Identifier: BSD-2-Clause */
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "rf.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

/** @brief Exercise the actual RF-enabled application loop using host-only drivers. */
void app_main(void);

static jmp_buf finished;
static uint32_t clock_us;
static unsigned reads, writes, starts, stops, sends, injected;
static int stalled;
static size_t input_used, output_used;
static char output[1024];
static const char input[] = "1 RFSTART 1234abcd\n2 RFTX 1234abcd 7 39 0 0\n";
static const char expected[] = "RSP 1 OK active=1 channel=26\n"
			       "RSP 2 OK queued=1 seq=7\n";
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

/** @brief Admit a synthetic radio session without touching hardware.
 * @return Zero for success.
 */
static int start(void)
{
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
	now_us, start, stop, send_frame, poll, power_dbm,
};

int usb_serial_jtag_driver_install(
	const usb_serial_jtag_driver_config_t *config)
{
	assert(config->tx_buffer_size == 2048 &&
	       config->rx_buffer_size == 2048);
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
	if (stalled && stops) {
		assert(starts == 1 && sends == 1);
		assert(clock_us < 100000);
		longjmp(finished, 1);
	}
}

int usb_serial_jtag_read_bytes(void *buffer, size_t length, uint32_t ticks)
{
	static const uint8_t mac[] = { 0x01, 0x88, 0x00 };
	size_t remaining = sizeof(input) - 1 - input_used;

	assert(++reads < 100);
	if (remaining) {
		assert(remaining <= length);
		memcpy(buffer, input + input_used, remaining);
		input_used += remaining;
		return (int)remaining;
	}
	if (!injected) {
		assert(sends == 1 && !stalled);
		ec_rf_tx_complete(1);
		ec_rf_receive(mac, sizeof(mac), 0, -40, 100, 123);
		injected = 1;
	}
	if (output_used ==
	    sizeof(expected) + sizeof(tx_event) + sizeof(rx_event) - 3) {
		assert(!memcmp(output, expected, sizeof(expected) - 1));
		assert(strstr(output, tx_event) && strstr(output, rx_event));
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
	if (stalled && sends)
		return 0;
	assert(output_used + count < sizeof(output));
	memcpy(output + output_used, buffer, count);
	output_used += count;
	output[output_used] = '\0';
	return (int)count;
}

/** @brief Run each real-loop scenario with independent synthetic boot state.
 * @param stall Nonzero blocks USB writes after TX admission until RF timeout.
 */
static void scenario(int stall)
{
	clock_us = reads = writes = starts = stops = sends = injected = 0;
	input_used = output_used = 0;
	stalled = stall;
	if (!setjmp(finished)) {
		app_main();
		assert(!"application unexpectedly returned");
	}
}

/** @brief Prove idle-input event delivery and RF timeout under USB backpressure.
 * @return Zero when the actual RF-enabled loop preserves both guarantees.
 */
int main(void)
{
	scenario(0);
	scenario(1);
	puts("ESP32-C6 RF events and blocked USB: PASS");
	return 0;
}
