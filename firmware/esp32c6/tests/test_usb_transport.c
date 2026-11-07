/* SPDX-License-Identifier: BSD-2-Clause */
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "version.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

/** @brief Execute the real ESP32-C6 diagnostic adapter against host-only stubs. */
void app_main(void);

static jmp_buf finished;
static int install_result;
static unsigned reads, writes, delays;
static size_t input_offset, output_used, outstanding;
static char output[1024];
static const char input[] = "1 PING\n2 VERSION\n3 ARM 1 60\n4 SEND 1 0188\n";
static const char expected[] =
	"RSP 1 OK EC-GATEWAY 3\n"
	"RSP 2 OK fw_version=" EC_VERSION
	" board=seeed-xiao-esp32c6 proto=3 update=none experimental=1 "
	"build=xiao-bringup-1\n"
	"RSP 3 ERR UNSUPPORTED experimental=1\n"
	"RSP 4 ERR UNSUPPORTED experimental=1\n";

int usb_serial_jtag_driver_install(
	const usb_serial_jtag_driver_config_t *config)
{
	assert(config->tx_buffer_size == 2048);
	assert(config->rx_buffer_size == 2048);
	return install_result;
}

void esp_log_level_set(const char *tag, int level)
{
	assert(!strcmp(tag, "*"));
	assert(level == ESP_LOG_NONE);
}

void vTaskDelay(uint32_t ticks)
{
	assert(ticks > 0 && ticks <= 10);
	assert(++delays < 1000);
}

int usb_serial_jtag_read_bytes(void *buffer, size_t length, uint32_t ticks)
{
	size_t available = sizeof(input) - 1 - input_offset;
	size_t count;

	assert(ticks > 0 && ticks <= 10);
	assert(outstanding == 0);
	assert(++reads < 100);
	if (reads == 1)
		return -1;
	if (reads == 2) {
		/* The receive error must yield before another attempt. */
		assert(delays > 0);
		return 0;
	}
	if (!available) {
		assert(output_used == sizeof(expected) - 1);
		assert(!memcmp(output, expected, output_used));
		longjmp(finished, 1);
	}
	/* Split requests across chunks while also placing multiple lines in a chunk. */
	count = available > 17 ? 17 : available;
	assert(count <= length);
	memcpy(buffer, input + input_offset, count);
	input_offset += count;
	return (int)count;
}

int usb_serial_jtag_write_bytes(const void *buffer, size_t length,
				uint32_t ticks)
{
	size_t count;

	assert(ticks == 0 && length > 0);
	assert(++writes < 1000);
	outstanding = length;
	if (writes == 1)
		return -1;
	if (writes == 2)
		return 0;
	count = length > 3 ? 3 : length;
	assert(output_used + count <= sizeof(expected) - 1);
	/* Every retry must begin exactly at the first byte not yet accepted. */
	assert(!memcmp(buffer, expected + output_used, count));
	memcpy(output + output_used, buffer, count);
	output_used += count;
	outstanding -= count;
	return (int)count;
}

/** @brief Prove setup failure and transient I/O cannot corrupt diagnostic replies.
 * @return Zero after complete, ordered responses and control rejection are verified.
 */
int main(void)
{
	install_result = -1;
	app_main();
	assert(reads == 0 && writes == 0);
	install_result = ESP_OK;
	if (!setjmp(finished)) {
		app_main();
		assert(!"adapter unexpectedly returned");
	}
	assert(reads > 2 && writes > 2 && delays > 2);
	puts("ESP32-C6 actual USB adapter: PASS");
	return 0;
}
