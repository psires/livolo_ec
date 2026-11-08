/* SPDX-License-Identifier: BSD-2-Clause */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../trace.c"
#include "transport.h"

static uint32_t clock_us;
static unsigned starts, stops, sends;
static int stop_result;

/** @brief Supply deterministic modulo-32 time for lease tests.
 * @return Current fixture clock.
 */
static uint32_t now_us(void)
{
	return clock_us;
}
/** @brief Start only the passive channel and create a new diagnostic epoch.
 * @param channel Requested channel.
 * @return Zero after asserting the receive-only fence.
 */
static int start(unsigned channel)
{
	assert(channel == 11);
	starts++;
	ec_mg24_trace_reset();
	return 0;
}
/** @brief Model a synchronous stop before allowing immutable trace retrieval.
 * @return Injected stop result, including failure, after freezing the writer.
 */
static int stop(void)
{
	stops++;
	ec_mg24_trace_freeze();
	return stop_result;
}
/** @brief Detect any unexpected TX admission through the real portable core.
 * @param mac Unused packet.
 * @param length Unused byte count.
 * @param cca Unused CCA mode.
 * @return Failure; every test requires this function never be called.
 */
static int send(const uint8_t *mac, size_t length, unsigned cca)
{
	(void)mac;
	(void)length;
	(void)cca;
	sends++;
	return -1;
}
/** @brief Declare the fixture's harmless configured power.
 * @return Zero dBm, without any actual radio.
 */
static int power(void)
{
	return 0;
}
/** @brief Model an idle SDK with no deferred receive work. */
static void poll(void)
{
}
static const struct ec_rf_ops ops = { .now_us = now_us,
				      .start = start,
				      .stop = stop,
				      .send = send,
				      .power_dbm = power,
				      .poll = poll };

/** @brief Require a complete response containing the expected semantic field.
 * @param command Input command without request identifier.
 * @param expected Required substring.
 */
static void expect(const char *command, const char *expected)
{
	char output[640];
	size_t n = ec_mg24_trace_command(command, output, sizeof(output));
	assert(n && n == strlen(output) && strstr(output, expected));
}

/** @brief Exercise real-core TX fencing, idle drain gate, lease and identity. */
static void test_protocol(void)
{
	struct ec_experimental parser;
	char output[640];
	const char *caps = "1 CAPS\n";
	ec_rf_setup(&ops);
	ec_experimental_init(&parser, "seeed-xiao-mg24", "xiao-trace-1",
			     115200);
	parser.radio_command = ec_mg24_trace_command;
	parser.adapter_diagnostics = 1;
	for (const char *c = caps; *c; c++)
		ec_experimental_byte(&parser, (uint8_t)*c, output,
				     sizeof(output));
	assert(strstr(output, "radio_tx=0") &&
	       strstr(output, "trace_diag=events_v1"));
	expect("RFSTART 00000001", "ERR RF_RX_ONLY");
	expect("RFTX invalid", "ERR RF_RX_ONLY");
	assert(!starts && !sends);
	expect("RFLISTEN 00000001", "active=1");
	expect("RFTRACE", "ERR RF_BUSY_OR_FAULT");
	expect("RFLISTEN 00000002", "ERR RF_BUSY_OR_FAULT");
	assert(starts == 1);
	ec_rf_receive((const uint8_t *)"abcde", 3, 1, -40, 100, clock_us);
	expect("RFSTOP", "active=0");
	expect("RFTRACE", "ERR RF_BUSY_OR_FAULT");
	assert(ec_rf_event(output, sizeof(output)));
	expect("RFTRACE", "frozen=1");
	expect("RFLISTEN 00000003", "active=1");
	clock_us += 60000001;
	ec_rf_poll();
	expect("RFTRACE", "frozen=1");
	assert(stops == 2 && starts == 2 && sends == 0);
	expect("RFLISTEN 00000004", "active=1");
	stop_result = -1;
	expect("RFSTOP", "ERR RF_STOP");
	expect("RFTRACE", "ERR RF_BUSY_OR_FAULT");
	stop_result = 0;
	ec_rf_setup(&ops);
}

/** @brief Preserve merged masks, wrapping timestamps, frozen records and epochs. */
static void test_event_storage(void)
{
	char output[640];
	ec_mg24_trace_reset();
	uint32_t old_epoch = epoch;
	ec_mg24_trace_event(UINT32_MAX - 2, UINT64_C(0x8000000100000005));
	ec_mg24_trace_event(7, 2);
	ec_mg24_trace_callback_cost(9);
	ec_mg24_trace_callback_cost(3);
	ec_mg24_trace_freeze();
	ec_mg24_trace_event(8, 99);
	ec_mg24_trace_callback_cost(100);
	expect("RFTRACE", "event_seen=2 event_stored=2 event_omitted=0");
	expect("RFTRACE", "callback_max_us=9");
	expect("RFTRACE EVENT 0", "ticks=4294967293 mask=8000000100000005");
	expect("RFTRACE EVENT 1", "seq=2 ticks=7 mask=0000000000000002");
	expect("RFTRACE EVENT 2", "ERR RF_TRACE_INDEX");
	const char *bad[] = { "RFTRACE ",
			      "RFTRACE EVENT",
			      "RFTRACE EVENT -1",
			      "RFTRACE EVENT 01",
			      "RFTRACE EVENT 4294967296",
			      "RFTRACE EVENT 0 x",
			      "RFTRACE ANCHOR 0 ",
			      "RFTRACEX",
			      "RFTRACE MARK +0" };
	for (unsigned i = 0; i < sizeof(bad) / sizeof(bad[0]); i++)
		expect(bad[i], "ERR RF_TRACE_SYNTAX");
	size_t n = ec_mg24_trace_command("RFTRACE EVENT 0", output,
					 sizeof(output));
	for (size_t cap = 0; cap <= n; cap++) {
		char guarded[640];
		memset(guarded, 0x5a, sizeof(guarded));
		assert(!ec_mg24_trace_command("RFTRACE EVENT 0", guarded, cap));
		assert(guarded[cap] == 0x5a);
	}
	assert(ec_mg24_trace_command("RFTRACE EVENT 0", output, n + 1) == n);
	ec_mg24_trace_reset();
	assert(epoch == old_epoch + 1);
	ec_mg24_trace_freeze();
	expect("RFTRACE EVENT 0", "ERR RF_TRACE_INDEX");
}

/** @brief Prove trace buffers never overwrite and counters saturate explicitly. */
static void test_capacity_and_anchors(void)
{
	uint8_t data[127];
	memset(data, 0xa5, sizeof(data));
	struct ec_mg24_trace_metadata meta = { .poll_ticks = 99,
					       .packet_ticks = 0,
					       .time_position = 0,
					       .time_valid = 0,
					       .packet_status = 6,
					       .air_bytes = 128 };
	ec_mg24_trace_reset();
	for (unsigned i = 0; i < EC_MG24_TRACE_EVENTS + 3; i++)
		ec_mg24_trace_event(i, UINT64_C(1) << 40);
	for (unsigned i = 0; i < EC_MG24_TRACE_ANCHORS + 2; i++)
		ec_mg24_trace_anchor(&meta, data, sizeof(data));
	for (unsigned i = 0; i < EC_MG24_TRACE_MARKS + 1; i++)
		ec_mg24_trace_mark(EC_TRACE_CALIBRATE, i, 0);
	ec_mg24_trace_anchor(NULL, data, sizeof(data));
	ec_mg24_trace_anchor(&meta, NULL, sizeof(data));
	meta.time_valid = 2;
	ec_mg24_trace_anchor(&meta, data, sizeof(data));
	ec_mg24_trace_freeze();
	expect("RFTRACE", "event_seen=4099 event_stored=4096 event_omitted=3");
	expect("RFTRACE", "anchor_seen=517 anchor_stored=512 anchor_omitted=5 "
			  "anchor_invalid=3");
	expect("RFTRACE", "mark_seen=129 mark_stored=128 mark_omitted=1");
	expect("RFTRACE EVENT 0", "seq=1 ticks=0 mask=0000010000000000");
	expect("RFTRACE ANCHOR 0", "time_position=0 time_valid=0");
	expect("RFTRACE ANCHOR 511", "len=127 end_ticks=0 end_valid=0 "
				     "end_status=0 fcs=original data=a5a5");
	char out[640];
	size_t n =
		ec_mg24_trace_command("RFTRACE ANCHOR 511", out, sizeof(out));
	for (size_t cap = 0; cap <= n; cap++) {
		memset(out, 0x5a, sizeof(out));
		assert(!ec_mg24_trace_command("RFTRACE ANCHOR 511", out, cap));
		assert(out[cap] == 0x5a);
	}
	assert(ec_mg24_trace_command("RFTRACE ANCHOR 511", out, n + 1) == n);
	expect("RFTRACE MARK 127", "kind=5 ticks=127 result=0");
	atomic_store(&event_seen, UINT32_MAX);
	atomic_store(&event_omitted, UINT32_MAX);
	anchor_seen = anchor_omitted = anchor_invalid = UINT32_MAX;
	mark_seen = mark_omitted = UINT32_MAX;
	atomic_store(&recording, 1);
	ec_mg24_trace_event(0, 1);
	ec_mg24_trace_anchor(NULL, NULL, 0);
	ec_mg24_trace_mark(EC_TRACE_STOP, 0, 0);
	ec_mg24_trace_freeze();
	assert(atomic_load(&event_seen) == UINT32_MAX &&
	       atomic_load(&event_omitted) == UINT32_MAX);
	assert(anchor_seen == UINT32_MAX && anchor_omitted == UINT32_MAX &&
	       anchor_invalid == UINT32_MAX);
	assert(mark_seen == UINT32_MAX && mark_omitted == UINT32_MAX);
}

/** @brief Fit full stores and maximum record indices through the 512-byte body. */
static void test_worst_case_responses(void)
{
	struct ec_experimental parser;
	uint8_t data[127];
	char output[640], expected[640];
	struct ec_mg24_trace_metadata meta = { .poll_ticks = UINT32_MAX,
					       .packet_ticks = UINT32_MAX,
					       .time_position = UINT32_MAX,
					       .time_valid = 1,
					       .packet_status = UINT32_MAX,
					       .air_bytes = 128,
					       .end_ticks = UINT32_MAX,
					       .end_valid = 1,
					       .end_status = UINT32_MAX };
	const char *commands[] = { "RFTRACE", "RFTRACE EVENT 4095",
				   "RFTRACE ANCHOR 511", "RFTRACE MARK 127" };
	memset(data, 0xff, sizeof(data));
	ec_mg24_trace_reset();
	atomic_store(&event_seen, UINT32_MAX);
	anchor_seen = mark_seen = UINT32_MAX;
	epoch = UINT32_MAX;
	for (unsigned i = 0; i < EC_MG24_TRACE_EVENTS; i++)
		ec_mg24_trace_event(UINT32_MAX, UINT64_MAX);
	for (unsigned i = 0; i < EC_MG24_TRACE_ANCHORS; i++)
		ec_mg24_trace_anchor(&meta, data, sizeof(data));
	for (unsigned i = 0; i < EC_MG24_TRACE_MARKS; i++)
		ec_mg24_trace_mark(EC_TRACE_STOP_RESULT, UINT32_MAX,
				   UINT32_MAX);
	assert(atomic_load(&event_stored) == EC_MG24_TRACE_EVENTS);
	assert(anchor_stored == EC_MG24_TRACE_ANCHORS);
	assert(mark_stored == EC_MG24_TRACE_MARKS);
	ec_mg24_trace_freeze();
	atomic_store(&event_omitted, UINT32_MAX);
	atomic_store(&callback_max_us, UINT32_MAX);
	atomic_store(&callback_reentry, 1);
	anchor_omitted = anchor_invalid = mark_omitted = UINT32_MAX;
	ec_experimental_init(&parser, "seeed-xiao-mg24", "xiao-trace-1",
			     115200);
	parser.radio_command = ec_mg24_trace_command;
	for (unsigned i = 0; i < sizeof(commands) / sizeof(commands[0]); i++) {
		size_t n = ec_mg24_trace_command(commands[i], expected, 512);
		assert(n && n < 512 && !strncmp(expected, "OK ", 3));
		for (size_t cap = 0; cap <= n; cap++) {
			memset(output, 0x5a, sizeof(output));
			assert(!ec_mg24_trace_command(commands[i], output + 1,
						      cap));
			assert(output[0] == 0x5a);
			for (size_t j = cap + 1; j < sizeof(output); j++)
				assert(output[j] == 0x5a);
		}
		memset(output, 0x5a, sizeof(output));
		assert(ec_mg24_trace_command(commands[i], output + 1, n + 1) ==
		       n);
		assert(!memcmp(output + 1, expected, n + 1));
		assert(output[0] == 0x5a);
		for (size_t j = n + 2; j < sizeof(output); j++)
			assert(output[j] == 0x5a);
		char line[100];
		int len = snprintf(line, sizeof(line), "4294967295 %s\n",
				   commands[i]);
		assert(len > 0 && len < (int)sizeof(line));
		size_t response = 0;
		for (int j = 0; j < len; j++)
			response =
				ec_experimental_byte(&parser, (uint8_t)line[j],
						     output, sizeof(output));
		assert(response == n + 16 && response == strlen(output));
		assert(!strncmp(output, "RSP 4294967295 ", 15));
		assert(!memcmp(output + 15, expected, n));
		assert(output[response - 1] == '\n');
	}
	ec_mg24_trace_reset();
	assert(epoch == 0);
	ec_mg24_trace_freeze();
	expect("RFTRACE", "epoch=0");
}

/** @brief Detect nested callback entry without overwriting the owning record. */
static void test_reentry(void)
{
	ec_mg24_trace_reset();
	assert(ec_mg24_trace_callback_enter());
	ec_mg24_trace_event(10, 1);
	assert(!ec_mg24_trace_callback_enter());
	assert(atomic_load(&callback_busy) == 1);
	ec_mg24_trace_callback_cost(4);
	assert(ec_mg24_trace_callback_enter());
	ec_mg24_trace_event(20, 2);
	ec_mg24_trace_callback_cost(3);
	ec_mg24_trace_freeze();
	assert(!ec_mg24_trace_callback_enter());
	expect("RFTRACE", "reentry=1");
	expect("RFTRACE", "event_seen=2 event_stored=2 event_omitted=0");
	ec_mg24_trace_mark(EC_TRACE_CALIBRATE, 30, 0);
	expect("RFTRACE", "mark_seen=0 mark_stored=0 mark_omitted=0");
	ec_mg24_trace_reset();
	ec_mg24_trace_freeze();
	expect("RFTRACE", "reentry=0");
}

/** @brief Run diagnostic storage and actual-core safety regressions.
 * @return Zero only after all bounded and receive-only assertions pass.
 */
int main(void)
{
	test_protocol();
	test_event_storage();
	test_capacity_and_anchors();
	test_worst_case_responses();
	test_reentry();
	puts("mg24 trace tests passed");
	return 0;
}
