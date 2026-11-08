/* SPDX-License-Identifier: BSD-2-Clause */
#include "reject.h"
#include "health.h"
#include "rf.h"
#include "transport.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

static unsigned starts, stops, sends;
static int stop_result;
static uint32_t clock_us;
static const uint8_t raw[] = { 5, 1, 0x88, 0, 0x12, 0x34 };

/** @brief Supply deterministic time without starting a real radio.
 * @return Synthetic microseconds modulo 2^32.
 */
static uint32_t now_us(void)
{
	return clock_us;
}

/** @brief Record adapter starts so rejected commands cannot hide side effects.
 * @param channel Requested IEEE channel; retained only through assertions.
 * @return Zero for the fake receiver.
 */
static int start(unsigned channel)
{
	assert(channel == 11 || channel == 26);
	starts++;
	return 0;
}

/** @brief Model both successful quiescence and a permanent stop fault.
 * @return The scenario's configured stop result.
 */
static int stop(void)
{
	stops++;
	return stop_result;
}

/** @brief Detect any transmit request that escapes the private parser fence.
 * @param mac Borrowed synthetic MAC bytes.
 * @param length MAC length excluding FCS.
 * @param cca Requested CCA policy.
 * @return Zero after recording the prohibited operation for assertions.
 */
static int send_frame(const uint8_t *mac, size_t length, unsigned cca)
{
	assert(mac && length && cca <= 1);
	sends++;
	return 0;
}

/** @brief Keep hardware callbacks under explicit native test control. */
static void poll(void)
{
}

/** @brief Report the allowed synthetic power without configuring hardware.
 * @return Zero dBm.
 */
static int power_dbm(void)
{
	return 0;
}

static const struct ec_rf_ops ops = {
	.now_us = now_us,
	.start = start,
	.stop = stop,
	.send = send_frame,
	.poll = poll,
	.power_dbm = power_dbm,
	.health = ec_mg24_health,
};

/** @brief Reset each scenario's radio, storage and counter evidence. */
static void reset(void)
{
	starts = stops = sends = clock_us = 0;
	stop_result = 0;
	ec_rf_setup(&ops);
	ec_mg24_health_reset();
	ec_mg24_reject_reset();
}

/** @brief Supply complete but explicitly untrusted packet metadata.
 * @return Metadata for raw, with a reported failed hardware CRC.
 */
static struct ec_mg24_reject_metadata metadata(void)
{
	return (struct ec_mg24_reject_metadata){
		.status = 4,
		.packet_bytes = sizeof(raw),
		.details_available = 1,
		.crc_passed = 0,
		.rssi = -42,
		.lqi = 87,
		.ticks = 123,
	};
}

/** @brief Check a diagnostic response without depending on its entire formatting.
 * @param command Complete private diagnostic command.
 * @param fragment Required response substring.
 */
static void expect(const char *command, const char *fragment)
{
	char response[512];
	size_t length =
		ec_mg24_reject_command(command, response, sizeof(response));

	assert(length && length == strlen(response));
	assert(strstr(response, fragment));
}

/** @brief Keep first-eight samples immutable while reporting every omitted record. */
static void test_storage_and_evidence(void)
{
	struct ec_mg24_reject_metadata meta = metadata();
	char first[512], again[512], summary[512], event[512];
	uint8_t mutable[sizeof(raw)];

	reset();
	memcpy(mutable, raw, sizeof(raw));
	ec_mg24_reject_record(&meta, mutable, sizeof(mutable));
	memset(mutable, 0xff, sizeof(mutable));
	assert(ec_mg24_reject_read(0, first, sizeof(first)));
	assert(strstr(first, "diag=untrusted_rx"));
	assert(strstr(first, "layout_complete=1"));
	assert(strstr(first, "fcs=original"));
	assert(strstr(first, "crc_passed=0"));
	assert(strstr(first, "data=050188001234"));
	for (unsigned i = 1; i < 10; i++) {
		meta.ticks = i;
		ec_mg24_reject_record(&meta, raw, sizeof(raw));
	}
	assert(ec_mg24_reject_read(0, again, sizeof(again)));
	assert(!strcmp(first, again));
	assert(ec_mg24_reject_read(7, again, sizeof(again)));
	assert(strstr(again, "ticks=7"));
	assert(ec_mg24_reject_read(8, again, sizeof(again)));
	assert(!strncmp(again, "ERR ", 4));
	strcpy(summary, "OK");
	assert(ec_mg24_reject_append_summary(summary, sizeof(summary), 2));
	assert(strstr(summary, "reject_seen=10"));
	assert(strstr(summary, "reject_stored=8"));
	assert(strstr(summary, "reject_omitted=2"));
	assert(!ec_rf_event(event, sizeof(event)));
	expect("RFSTATUS", "rx=0 dropped=0");
	assert(!starts && !stops && !sends);
}

/** @brief Distinguish incomplete bytes, unavailable details and invalid callers. */
static void test_invalid_and_incomplete(void)
{
	struct ec_mg24_reject_metadata meta = metadata();
	char response[512];
	uint8_t oversized[129];

	reset();
	meta.details_available = 0;
	meta.crc_passed = 1;
	ec_mg24_reject_record(&meta, raw, sizeof(raw) - 1);
	assert(ec_mg24_reject_read(0, response, sizeof(response)));
	assert(strstr(response, "truncated=1"));
	assert(strstr(response, "layout_complete=0"));
	assert(strstr(response, "fcs=unavailable"));
	assert(strstr(response, "crc_passed=unknown"));
	assert(strstr(response, "rssi=unknown"));
	assert(strstr(response, "lqi=unknown"));
	ec_mg24_reject_record(NULL, raw, sizeof(raw));
	ec_mg24_reject_record(&meta, NULL, sizeof(raw));
	ec_mg24_reject_record(&meta, raw, sizeof(raw) + 1);
	meta.details_available = 2;
	ec_mg24_reject_record(&meta, raw, sizeof(raw));
	meta = metadata();
	meta.crc_passed = 2;
	ec_mg24_reject_record(&meta, raw, sizeof(raw));
	strcpy(response, "OK");
	assert(ec_mg24_reject_append_summary(response, sizeof(response), 2));
	assert(strstr(response, "reject_seen=6"));
	assert(strstr(response, "reject_stored=1"));
	assert(strstr(response, "reject_invalid=5"));
	assert(strstr(response, "reject_omitted=5"));
	memset(oversized, 0x5a, sizeof(oversized));
	meta = metadata();
	meta.packet_bytes = sizeof(oversized);
	ec_mg24_reject_record(&meta, oversized, sizeof(oversized));
	assert(ec_mg24_reject_read(1, response, sizeof(response)));
	assert(strstr(response, "stored_bytes=128"));
	assert(strstr(response, "truncated=1"));
	assert(strstr(response, "fcs=unavailable"));
	meta = metadata();
	meta.packet_bytes = 0;
	ec_mg24_reject_record(&meta, NULL, 0);
	assert(ec_mg24_reject_read(2, response, sizeof(response)));
	assert(strstr(response, "layout_complete=0"));
	assert(strstr(response, "fcs=unavailable"));
	meta.packet_bytes = sizeof(raw);
	memcpy(oversized, raw, sizeof(raw));
	oversized[0] |= 0x80;
	ec_mg24_reject_record(&meta, oversized, sizeof(raw));
	assert(ec_mg24_reject_read(3, response, sizeof(response)));
	assert(strstr(response, "truncated=0"));
	assert(strstr(response, "layout_complete=0"));
	assert(strstr(response, "fcs=unavailable"));
}

/** @brief Check exact CRC tokens and read bounds for known and absent details. */
static void test_crc_format_and_bounds(void)
{
	static const char *known[] = {
		"crc_passed=0 rssi=-42 lqi=87 ",
		"crc_passed=1 rssi=-42 lqi=87 ",
	};
	const char *unknown = "crc_passed=unknown rssi=unknown lqi=unknown ";
	char expected[512], guarded[514];

	for (unsigned details = 0; details <= 1; details++) {
		for (unsigned crc = 0; crc <= 1; crc++) {
			struct ec_mg24_reject_metadata meta = metadata();
			size_t length;

			reset();
			meta.details_available = details;
			meta.crc_passed = crc;
			ec_mg24_reject_record(&meta, raw, sizeof(raw));
			length = ec_mg24_reject_read(0, expected,
						     sizeof(expected));
			assert(length && length == strlen(expected));
			assert(strstr(expected,
				      details ? known[crc] : unknown));
			for (size_t capacity = 0; capacity <= length;
			     capacity++) {
				memset(guarded, 0x5a, sizeof(guarded));
				assert(!ec_mg24_reject_read(0, guarded + 1,
							    capacity));
				assert(guarded[0] == 0x5a);
				for (size_t i = capacity + 1;
				     i < sizeof(guarded); i++)
					assert(guarded[i] == 0x5a);
			}
			memset(guarded, 0x5a, sizeof(guarded));
			assert(ec_mg24_reject_read(0, guarded + 1,
						   length + 1) == length);
			assert(!memcmp(guarded + 1, expected, length + 1));
			assert(guarded[0] == 0x5a);
			for (size_t i = length + 2; i < sizeof(guarded); i++)
				assert(guarded[i] == 0x5a);
		}
	}
}

/** @brief Reject nonboolean CRC flags even when their details are unavailable. */
static void test_invalid_crc_flags(void)
{
	static const uint32_t invalid_crc[] = { 2, UINT32_MAX };
	const char *summary =
		"OK reject_seen=1 reject_stored=0 reject_omitted=1 "
		"reject_truncated=0 reject_invalid=1 "
		"reject_count_mode=saturating";
	char response[512];

	for (unsigned details = 0; details <= 1; details++) {
		for (unsigned i = 0;
		     i < sizeof(invalid_crc) / sizeof(invalid_crc[0]); i++) {
			struct ec_mg24_reject_metadata meta = metadata();

			reset();
			meta.details_available = details;
			meta.crc_passed = invalid_crc[i];
			ec_mg24_reject_record(&meta, raw, sizeof(raw));
			assert(ec_mg24_reject_read(0, response,
						   sizeof(response)));
			assert(!strcmp(response, "ERR RF_REJECT_ABSENT"));
			strcpy(response, "OK");
			assert(ec_mg24_reject_append_summary(
				       response, sizeof(response), 2) ==
			       strlen(summary));
			assert(!strcmp(response, summary));
		}
	}
}

/** @brief Bound worst-case formatting and saturate counters without wrapping. */
static void test_bounds_and_saturation(void)
{
	struct ec_mg24_reject_metadata meta = metadata();
	uint8_t bytes[128];
	char response[513], repeated[512];
	size_t length;

	reset();
	memset(bytes, 0xff, sizeof(bytes));
	meta.status = meta.packet_bytes = meta.lqi = meta.ticks = UINT32_MAX;
	meta.rssi = INT32_MIN;
	ec_mg24_reject_record(&meta, bytes, sizeof(bytes));
	length = ec_mg24_reject_read(0, repeated, sizeof(repeated));
	assert(length && length < 512 && length == strlen(repeated));
	for (size_t capacity = 0; capacity <= length; capacity++) {
		memset(response, 0x5a, sizeof(response));
		assert(!ec_mg24_reject_read(0, response, capacity));
		assert(response[capacity] == 0x5a);
	}
	assert(ec_mg24_reject_read(0, response, length + 1) == length);
	assert(!strcmp(response, repeated));
	ec_mg24_reject_test_seed(UINT32_MAX - 1);
	ec_mg24_reject_record(NULL, NULL, 0);
	ec_mg24_reject_record(NULL, NULL, 0);
	strcpy(response, "OK");
	assert(ec_mg24_reject_append_summary(response, sizeof(response), 2));
	assert(strstr(response, "reject_seen=4294967295"));
	assert(strstr(response, "reject_omitted=4294967295"));
	assert(strstr(response, "reject_invalid=4294967295"));
	assert(strstr(response, "reject_stored=1"));
	assert(strstr(response, "reject_count_mode=saturating"));
	assert(ec_mg24_health(response, 512));
	assert(strlen(response) < 512);
	assert(!ec_mg24_reject_append_summary(response, 2, 2));
	assert(!ec_mg24_reject_read(0, NULL, 0));
	assert(!ec_mg24_reject_append_summary(NULL, 0, 0));
	ec_mg24_reject_reset();
	expect("RFREJECT 0", "ERR ");
}

/** @brief Reject TX and unsafe reads through the actual core state machine. */
static void test_parser_fences(void)
{
	struct ec_mg24_reject_metadata meta = metadata();
	char event[512], response[512];
	static const char *invalid[] = { "RFREJECT",	"RFREJECT ",
					 "RFREJECT 8",	"RFREJECT -1",
					 "RFREJECT 00", "RFREJECT 0 ",
					 "RFREJECT 0 x" };

	reset();
	ec_mg24_reject_record(&meta, raw, sizeof(raw));
	expect("RFSTART 1234abcd", "ERR RF_RX_ONLY");
	expect("RFTX 1234abcd 1 39 0 0", "ERR RF_RX_ONLY");
	for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++)
		expect(invalid[i], "ERR ");
	assert(!starts && !stops && !sends);
	expect("RFREJECT 0", "diag=untrusted_rx");
	expect("RFLISTEN 1234abcd", "OK active=1 channel=11 mode=passive");
	assert(starts == 1);
	expect("RFREJECT 0", "ERR ");
	expect("RFLISTEN 1234abcd", "ERR ");
	expect("RFSTART 1234abcd", "ERR RF_RX_ONLY");
	expect("RFTX 1234abcd 1 39 0 0", "ERR RF_RX_ONLY");
	assert(starts == 1 && !sends);
	/* Diagnostic storage itself must never publish a valid receive event. */
	ec_mg24_reject_record(&meta, raw, sizeof(raw));
	assert(!ec_rf_event(event, sizeof(event)));
	expect("RFSTATUS", "rx=0 dropped=0");
	ec_rf_receive(raw + 1, 3, 1, -42, 87, 123);
	expect("RFSTOP", "OK");
	assert(!ec_rf_diagnostic_idle());
	expect("RFREJECT 0", "ERR ");
	expect("RFLISTEN 1234abcd", "ERR ");
	assert(ec_rf_event(event, sizeof(event)));
	assert(ec_rf_diagnostic_idle());
	expect("RFREJECT 0", "diag=untrusted_rx");
	stop_result = -1;
	expect("RFLISTEN 1234abcd", "OK");
	ec_mg24_reject_command("RFSTOP", response, sizeof(response));
	assert(!ec_rf_diagnostic_idle());
	expect("RFREJECT 0", "ERR ");
	expect("RFLISTEN 1234abcd", "ERR ");
	assert(!sends);
	/* Inject core TX state directly only to exercise the private idle-read gate. */
	reset();
	assert(ec_rf_command("RFSTART 1234abcd", response, sizeof(response)));
	assert(ec_rf_command("RFTX 1234abcd 1 39 0 0", response,
			     sizeof(response)));
	assert(sends == 1 && !ec_rf_diagnostic_idle());
	expect("RFREJECT 0", "ERR ");
	ec_rf_tx_complete(1);
	ec_rf_poll();
	assert(ec_rf_command("RFSTOP", response, sizeof(response)));
	assert(!ec_rf_diagnostic_idle());
	expect("RFREJECT 0", "ERR ");
	assert(ec_rf_event(event, sizeof(event)));
	assert(strstr(event, "RF TX "));
	assert(ec_rf_diagnostic_idle());
}

/** @brief Advertise receive-only private capabilities through the actual parser. */
static void test_capabilities(void)
{
	struct ec_experimental parser;
	char response[640];
	const char *command = "1 CAPS\n";
	size_t length = 0;

	reset();
	ec_experimental_init(&parser, "seeed-xiao-mg24", "xiao-reject-1",
			     115200);
	parser.radio_command = ec_mg24_reject_command;
	while (*command)
		length = ec_experimental_byte(&parser, *command++, response,
					      sizeof(response));
	assert(length && length == strlen(response));
	assert(strstr(response, "radio_tx=0"));
	assert(strstr(response, "rf_diag=none"));
	assert(strstr(response, "reject_diag=untrusted_v1"));
	assert(strstr(response, "queue_diag=ring_v1"));
	assert(!strstr(response, "synthetic_only"));
	assert(!starts && !stops && !sends);
}

/** @brief Validate private rejected-frame diagnostics entirely without hardware.
 * @return Zero after storage, formatting, parser fences and capabilities pass.
 */
int main(void)
{
	test_storage_and_evidence();
	test_invalid_and_incomplete();
	test_crc_format_and_bounds();
	test_invalid_crc_flags();
	test_bounds_and_saturation();
	test_parser_fences();
	test_capabilities();
	puts("MG24 untrusted RX storage and receive-only fences: PASS");
	return 0;
}
