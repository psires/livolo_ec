/* SPDX-License-Identifier: BSD-2-Clause */
#include "health.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static const char empty[] =
	"OK rx_hold_failed=0 rx_fifo_overflow=0 rx_frame_error=0 "
	"rx_packet_status_invalid=0 rx_packet_length_invalid=0 "
	"rx_packet_details_failed=0 rx_packet_layout_invalid=0 "
	"rx_calibration_failed=0 counter_bits=32 scope=last_start_attempt";

/** @brief Verify that distinct reasons remain attributable across diagnostic reads. */
static void test_reason_evidence(void)
{
	char report[512], repeated[512];
	size_t length;

	ec_mg24_health_reset();
	assert(ec_mg24_health(report, sizeof(report)) == strlen(empty));
	assert(!strcmp(report, empty));
	for (unsigned reason = 0; reason < EC_MG24_REASON_COUNT; reason++)
		for (unsigned n = 0; n <= reason; n++)
			ec_mg24_health_count((enum ec_mg24_rx_reason)reason);
	length = ec_mg24_health(report, sizeof(report));
	assert(length && length == strlen(report));
	assert(strstr(report,
		      "rx_hold_failed=1 rx_fifo_overflow=2 rx_frame_error=3 "));
	assert(strstr(
		report,
		"rx_packet_status_invalid=4 rx_packet_length_invalid=5 "));
	assert(strstr(
		report,
		"rx_packet_details_failed=6 rx_packet_layout_invalid=7 "));
	assert(strstr(report, "rx_calibration_failed=8 counter_bits=32 "
			      "scope=last_start_attempt"));
	ec_mg24_health_count((enum ec_mg24_rx_reason) - 1);
	ec_mg24_health_count(EC_MG24_REASON_COUNT);
	assert(ec_mg24_health(repeated, sizeof(repeated)) == length);
	assert(!strcmp(report, repeated));
	ec_mg24_health_reset();
	assert(ec_mg24_health(report, sizeof(report)) == strlen(empty));
	assert(!strcmp(report, empty));
}

/** @brief Retain overlapping hardware occurrences without conflating them as packets. */
static void test_overlapping_occurrences(void)
{
	char report[512];

	ec_mg24_health_reset();
	ec_mg24_health_count(EC_MG24_FIFO_OVERFLOW);
	ec_mg24_health_count(EC_MG24_FRAME_ERROR);
	assert(ec_mg24_health(report, sizeof(report)));
	assert(strstr(report,
		      "rx_hold_failed=0 rx_fifo_overflow=1 rx_frame_error=1 "));
	assert(strstr(report, "rx_packet_status_invalid=0 "));
}

/** @brief Suppress truncated replies and keep caller memory beyond capacity intact. */
static void test_reply_bounds(void)
{
	char report[512];
	size_t length;

	ec_mg24_health_reset();
	memset(report, 0x5a, sizeof(report));
	assert(!ec_mg24_health(report, 0));
	assert(report[0] == 0x5a);
	assert(!ec_mg24_health(report, 8));
	assert(report[7] == '\0' && report[8] == 0x5a);
	length = strlen(empty);
	assert(!ec_mg24_health(report, length));
	assert(ec_mg24_health(report, length + 1) == length);
	assert(!strcmp(report, empty));
}

/** @brief Exercise MG24 diagnostic semantics without initializing a radio or SDK.
 * @return Zero when attribution, reset, read-only snapshots and bounds hold.
 */
int main(void)
{
	test_reason_evidence();
	test_overlapping_occurrences();
	test_reply_bounds();
	puts("MG24 receive-reason diagnostics: PASS");
	return 0;
}
