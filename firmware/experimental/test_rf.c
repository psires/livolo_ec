/* SPDX-License-Identifier: BSD-2-Clause */
#include "rf.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t clock_us;
static unsigned starts, stops, sends, polls;
static int start_result, send_result, stop_result, power;
static const uint8_t *borrowed;
static size_t borrowed_length;
static uint8_t transmitted[125];
static char response[1024];

/** @brief Supply a controllable wrapping microsecond clock.
 * @return Current mock time, modulo 2^32.
 */
static uint32_t now_us(void)
{
	return clock_us;
}

/** @brief Record explicit radio-start requests without producing RF.
 * @return Injected adapter result; zero means success.
 */
static int start(void)
{
	starts++;
	return start_result;
}

/** @brief Model cancellation success or an unconfirmed hardware stop.
 * @return Zero releases borrowed storage; nonzero preserves uncertain ownership.
 */
static int stop(void)
{
	stops++;
	if (!stop_result)
		borrowed = NULL;
	return stop_result;
}

/** @brief Retain an asynchronous TX buffer to detect premature reuse.
 * @param mac Borrowed MAC bytes, which must remain stable until completion.
 * @param length Readable MAC bytes, excluding FCS.
 * @param cca One for a CCA attempt, zero for immediate transmission.
 * @return Injected adapter result; zero admits one asynchronous operation.
 */
static int send_frame(const uint8_t *mac, size_t length, unsigned cca)
{
	assert(!borrowed);
	assert(length == 39 || length == 41);
	assert(cca <= 1);
	sends++;
	if (send_result)
		return send_result;
	borrowed = mac;
	borrowed_length = length;
	memcpy(transmitted, mac, length);
	return 0;
}

/** @brief Count main-loop adapter servicing independently of USB output. */
static void poll(void)
{
	polls++;
}

/** @brief Report the injected adapter transmit power.
 * @return Mock power in dBm.
 */
static int power_dbm(void)
{
	return power;
}

static const struct ec_rf_ops ops = {
	.now_us = now_us,
	.start = start,
	.stop = stop,
	.send = send_frame,
	.poll = poll,
	.power_dbm = power_dbm,
};

/** @brief Start a fresh synthetic boot with no radio ownership. */
static void reset(void)
{
	clock_us = 1000000;
	starts = stops = sends = polls = 0;
	start_result = send_result = stop_result = power = 0;
	borrowed = NULL;
	borrowed_length = 0;
	ec_rf_setup(&ops);
	assert(!starts && !stops && !sends);
}

/** @brief Require one complete response body to a diagnostic command.
 * @param text Full command body, without UART request ID.
 * @param success Nonzero requires OK; zero requires ERR.
 */
static void command(const char *text, int success)
{
	size_t length = ec_rf_command(text, response, sizeof(response));

	assert(length > 0 && length < sizeof(response));
	assert(strlen(response) == length);
	assert(!strncmp(response, success ? "OK" : "ERR", success ? 2 : 3));
}

/** @brief Complete one borrowed transmission and retrieve its separate event.
 * @param success Nonzero models actual hardware transmission completion.
 */
static void complete(unsigned success)
{
	assert(borrowed);
	assert(!memcmp(borrowed, transmitted, borrowed_length));
	borrowed = NULL;
	ec_rf_tx_complete(success);
	ec_rf_poll();
	assert(ec_rf_event(response, sizeof(response)));
	assert(strstr(response, "RF TX "));
	assert(strstr(response, success ? "status=done" : "status=failed"));
}

/** @brief Reject malformed or unauthenticated requests without any TX call. */
static void test_command_bounds(void)
{
	const char *invalid_start[] = {
		"RFSTART",	    "RFSTART 00000000",
		"RFSTART 1234abc",  "RFSTART 1234abcd0",
		"RFSTART 1234abcg", "RFSTART 1234abcd extra",
	};
	const char *invalid_tx[] = {
		"RFTX 1234abcd 0 38 0 0",
		"RFTX 1234abcd 0 42 0 0",
		"RFTX 1234abcd 0 125 0 0",
		"RFTX 1234abcd 0 126 0 0",
		"RFTX 1234abcd -1 39 0 0",
		"RFTX 1234abcd 256 39 0 0",
		"RFTX 1234abcd 4294967296 39 0 0",
		"RFTX 1234abcd 0 39 2 0",
		"RFTX 1234abcd 0 39 0 2",
		"RFTX 1234abcd 0 39 0",
		"RFTX 1234abcd 0 39 0 0 extra",
		"RFTX abcdef12 0 39 0 0",
		"RFTX 018800efbe2222",
	};

	reset();
	command("RFSTATUS", 1);
	command("RFTX 1234abcd 0 39 0 0", 0);
	for (size_t i = 0; i < sizeof(invalid_start) / sizeof(*invalid_start);
	     i++)
		command(invalid_start[i], 0);
	assert(!starts && !sends);
	command("RFSTART 1234abcd", 1);
	for (size_t i = 0; i < sizeof(invalid_tx) / sizeof(*invalid_tx); i++)
		command(invalid_tx[i], 0);
	assert(starts == 1 && !sends);
	command("RFSTOP", 1);
	command("RFTX 1234abcd 0 39 0 0", 0);
	assert(stops && !sends);
}

/** @brief Preserve pending TX bytes, rate limits and one-use sequence numbers. */
static void test_async_ownership(void)
{
	reset();
	command("RFSTART 1234abcd", 1);
	command("RFTX 1234abcd 7 39 0 0", 1);
	assert(sends == 1 && borrowed_length == 39);
	assert(transmitted[0] == 0x01 && transmitted[1] == 0x88);
	assert(transmitted[2] == 7);
	assert(!memcmp(transmitted + 11, "XRF1\x12\x34\xab\xcd", 8));
	assert(!ec_rf_event(response, sizeof(response)));
	clock_us += 1000;
	command("RFTX 1234abcd 8 41 1 1", 0);
	assert(sends == 1);
	complete(1);
	clock_us += 99000;
	command("RFTX 1234abcd 7 39 0 0", 0);
	command("RFTX 1234abcd 8 41 1 1", 1);
	assert(sends == 2 && transmitted[0] == 0x21);
	complete(0);
	clock_us += 99999;
	command("RFTX 1234abcd 9 39 0 0", 0);
	clock_us++;
	command("RFTX 1234abcd 9 39 0 0", 1);
	complete(1);
}

/** @brief Stop a lost completion and prohibit reuse even after a late callback. */
static void test_timeout_fault(void)
{
	reset();
	command("RFSTART 1234abcd", 1);
	command("RFTX 1234abcd 0 39 0 0", 1);
	clock_us += 19999;
	ec_rf_poll();
	assert(!stops && borrowed);
	clock_us++;
	ec_rf_poll();
	assert(stops && !borrowed);
	assert(ec_rf_event(response, sizeof(response)));
	assert(strstr(response, "status=timeout"));
	ec_rf_tx_complete(1);
	ec_rf_poll();
	command("RFSTOP", 1);
	command("RFSTART abcdef12", 0);
	command("RFTX 1234abcd 1 39 0 0", 0);
	assert(sends == 1);
}

/** @brief Enforce transmission budget and expiry across a microsecond wrap. */
static void test_budget_and_lease(void)
{
	char text[80];

	reset();
	command("RFSTART 1234abcd", 1);
	for (unsigned i = 0; i < 64; i++) {
		snprintf(text, sizeof(text), "RFTX 1234abcd %u 39 0 0", i);
		command(text, 1);
		complete(1);
		clock_us += 100000;
	}
	command("RFTX 1234abcd 64 39 0 0", 0);
	assert(sends == 64);
	reset();
	clock_us = UINT32_MAX - 1000;
	command("RFSTART 1234abcd", 1);
	clock_us += 59999999;
	ec_rf_poll();
	assert(!stops);
	clock_us++;
	ec_rf_poll();
	assert(stops);
	command("RFTX 1234abcd 0 39 0 0", 0);
	assert(!sends);
}

/** @brief Preserve complete bounded RX events and count malformed/overflow drops. */
static void test_receive_queue(void)
{
	uint8_t packet[127];
	unsigned events = 0;

	memset(packet, 0xa5, sizeof(packet));
	reset();
	ec_rf_receive(packet, 39, 0, -30, 100, 0);
	assert(!ec_rf_event(response, sizeof(response)));
	command("RFSTART 1234abcd", 1);
	ec_rf_receive(packet, 125, 1, -45, 200, 123);
	assert(!ec_rf_event(response, 4));
	assert(ec_rf_event(response, sizeof(response)));
	assert(strstr(response, "len=125") && strstr(response, "fcs=included"));
	assert(strstr(response, "rssi=-45") && strstr(response, "ticks=123"));
	assert(response[strlen(response) - 1] == '\n');
	ec_rf_receive(packet, 1, 0, -30, 100, 0);
	assert(ec_rf_event(response, sizeof(response)));
	assert(strstr(response, "len=1 fcs=stripped"));
	for (unsigned i = 0; i < 8; i++)
		ec_rf_receive(packet, 39, 0, -30, 100, i);
	while (ec_rf_event(response, sizeof(response)))
		events++;
	assert(events == 7);
	ec_rf_receive(packet, 0, 0, -30, 100, 0);
	ec_rf_receive(packet, 126, 0, -30, 100, 0);
	ec_rf_receive(packet, 39, 2, -30, 100, 0);
	assert(!ec_rf_event(response, sizeof(response)));
	ec_rf_drop();
	command("RFSTATUS", 1);
	assert(strstr(response, "dropped=5"));
}

/** @brief Reject failed or overpowered initialization and distinguish TX rejection. */
static void test_adapter_failures(void)
{
	reset();
	start_result = -1;
	command("RFSTART 1234abcd", 0);
	assert(starts == 1 && stops == 1 && !sends);
	start_result = 0;
	command("RFSTART abcdef12", 0);
	assert(starts == 1);
	reset();
	power = 1;
	command("RFSTART 1234abcd", 0);
	assert(starts == 1 && stops == 1 && !sends);
	reset();
	command("RFSTART 1234abcd", 1);
	send_result = -1;
	command("RFTX 1234abcd 0 39 0 0", 0);
	assert(sends == 1 && !borrowed);
	assert(ec_rf_event(response, sizeof(response)));
	assert(strstr(response, "status=failed"));
	command("RFSTATUS", 1);
	assert(strstr(response, "tx_done=0"));
	assert(strstr(response, "tx_failed=1"));
	reset();
	command("RFSTART 1234abcd", 1);
	command("RFTX 1234abcd 0 39 0 0", 1);
	stop_result = -1;
	command("RFSTOP", 0);
	assert(stops == 1 && borrowed);
	command("RFSTART abcdef12", 0);
	command("RFTX 1234abcd 1 39 0 0", 0);
	assert(sends == 1);
}

/** @brief Execute portable RF safety regressions without initializing hardware.
 * @return Zero when all bounded diagnostic invariants hold.
 */
int main(void)
{
	test_command_bounds();
	test_async_ownership();
	test_timeout_fault();
	test_budget_and_lease();
	test_receive_queue();
	test_adapter_failures();
	puts("experimental RF bounds and ownership: PASS");
	return 0;
}
