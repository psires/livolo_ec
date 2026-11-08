/* SPDX-License-Identifier: BSD-2-Clause */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rail.h"
static int test_hold_result, test_start_result, test_release_result;
static unsigned available, status, packet_bytes, copied, released;
static unsigned core_calls, aggregate_drops, details_fail;
static uint8_t packet_data[128];
#ifdef EC_RF_TRACE_DIAGNOSTICS
static unsigned end_fail, missing_time;
/** @brief Validate derived length while modeling SDK timestamp conversion.
 * @param details Initialized copy; the original metadata must remain unchanged.
 * @return Selected conversion status.
 */
static int test_packet_end(RAIL_RxPacketDetails_t *details)
{
	assert(details->timeReceived.totalPacketBytes == packet_bytes);
	details->timeReceived.packetTime = 25;
	details->timeReceived.timePosition = RAIL_PACKET_TIME_AT_PACKET_END;
	return end_fail;
}
#endif

/** @brief Return one queued mock descriptor or the no-packet sentinel.
 * @param info Destination descriptor written only for an available packet.
 * @return Mock packet handle or invalid sentinel.
 */
static int test_packet_info(RAIL_RxPacketInfo_t *info)
{
	if (!available)
		return RAIL_RX_PACKET_HANDLE_INVALID;
	info->packetStatus = status;
	info->packetBytes = packet_bytes;
	return 1;
}
/** @brief Supply mock receive details or an injected SDK failure.
 * @param details Output metadata for the current packet.
 * @return Zero for success, nonzero for the injected failure.
 */
static int test_packet_details(RAIL_RxPacketDetails_t *details)
{
	details->rssi = -40;
	details->lqi = 255;
#ifdef EC_RF_TRACE_DIAGNOSTICS
	/* Populate only documented timestamp outputs; totalPacketBytes stays zero. */
	details->timeReceived.packetTime = UINT32_MAX - 50;
	details->timeReceived.timePosition =
		missing_time ? RAIL_PACKET_TIME_INVALID : 3;
#endif
	return details_fail;
}
/** @brief Copy the mock packet using the adapter's existing void-copy API.
 * @param bytes Output packet storage, at least packet_bytes bytes.
 */
static void test_packet_copy(uint8_t *bytes)
{
	memcpy(bytes, packet_data, packet_bytes);
	copied++;
}
/** @brief Consume a mock descriptor and return a selectable release status.
 * @return Zero for success or the injected SDK failure.
 */
static int test_packet_release(void)
{
	assert(available);
	available--;
	released++;
	return test_release_result;
}
#include "../adapter.c"
#include "../radio.c"

#ifdef EC_RF_TRACE_DIAGNOSTICS
/** @brief Permit trace snapshots in the quiescent adapter-only fixture.
 * @return True; actual core busy/drain gating is tested in test_trace.c.
 */
int ec_rf_diagnostic_idle(void)
{
	return 1;
}
/** @brief Refuse unused portable commands in the isolated radio fixture.
 * @param command Unused command.
 * @param out Unused destination.
 * @param capacity Unused capacity.
 * @return Zero; this fixture only reads private trace records.
 */
size_t ec_rf_command(const char *command, char *out, size_t capacity)
{
	(void)command;
	(void)out;
	(void)capacity;
	return 0;
}
#endif

/** @brief Observe portable handoff attempts without accepting or rejecting data.
 * @param data Borrowed mock MAC bytes.
 * @param length MAC byte length, excluding FCS.
 * @param included Whether original FCS bytes follow the MAC.
 * @param rssi Mock received signal level in dBm.
 * @param lqi Mock link quality.
 * @param ticks Existing adapter clock value in microseconds.
 */
void ec_rf_receive(const uint8_t *data, size_t length, unsigned included,
		   int rssi, unsigned lqi, uint32_t ticks)
{
	(void)data;
	assert(length == 3 && included == 1 && rssi == -40 && lqi == 255 &&
	       ticks == 123);
	core_calls++;
}
/** @brief Retain the original aggregate-drop behavior in the mock core. */
void ec_rf_drop(void)
{
	aggregate_drops++;
}
/** @brief Accept unrelated TX callbacks without affecting receive counters.
 * @param success Existing terminal TX outcome.
 */
void ec_rf_tx_complete(unsigned success)
{
	(void)success;
}
/** @brief Start a clean mock epoch with one valid complete packet. */
static void reset_fixture(void)
{
	ec_mg24_adapter_reset();
	ec_mg24_health_reset();
#ifdef EC_RF_TRACE_DIAGNOSTICS
	ec_mg24_trace_reset();
	end_fail = missing_time = 0;
#endif
	test_hold_result = 1;
	test_start_result = 0;
	test_release_result = 0;
	available = 1;
	status = RAIL_RX_PACKET_READY_SUCCESS;
	packet_bytes = 6;
	copied = released = core_calls = aggregate_drops = details_fail = 0;
	memset(packet_data, 0, sizeof(packet_data));
	packet_data[0] = 5;
	radio = (void *)1;
}
/** @brief Check independent handoff steps and unchanged overlapping drop policy. */
static void test_callbacks_and_release(void)
{
	reset_fixture();
	event(radio, RAIL_EVENT_RX_PACKET_RECEIVED |
			     RAIL_EVENT_RX_FIFO_OVERFLOW |
			     RAIL_EVENT_RX_FRAME_ERROR);
	assert(atomic_load(&counts[EC_MG24_ADAPTER_RX_EVENT]) == 1);
	assert(atomic_load(&counts[EC_MG24_ADAPTER_HOLD_OK]) == 1);
	assert(aggregate_drops == 1);
	poll();
	assert(copied == 1 && released == 1 && core_calls == 1 &&
	       aggregate_drops == 1);
	assert(atomic_load(&counts[EC_MG24_ADAPTER_RELEASE_OK]) == 1);
	reset_fixture();
	test_release_result = 1;
	poll();
	assert(released == 1 && core_calls == 1 && !aggregate_drops);
	assert(atomic_load(&counts[EC_MG24_ADAPTER_RELEASE_FAIL]) == 1);
	reset_fixture();
	test_hold_result = -1;
	event(radio, RAIL_EVENT_RX_PACKET_RECEIVED);
	assert(aggregate_drops == 1 &&
	       !atomic_load(&counts[EC_MG24_ADAPTER_HOLD_OK]));
}
/** @brief Keep invalid status, length, details and layout paths distinguishable. */
static void test_invalid_packets_and_budget(void)
{
	for (unsigned reason = 0; reason < 4; reason++) {
		reset_fixture();
		if (reason == 0)
			status = 99;
		if (reason == 1)
			packet_bytes = 129;
		if (reason == 2)
			details_fail = 1;
		if (reason == 3)
			packet_data[0] = 4;
		poll();
		assert(released == 1 && !core_calls && aggregate_drops == 1);
		assert(copied == (reason == 3));
	}
	reset_fixture();
	available = 5;
	poll();
	assert(available == 1 && core_calls == 4 && released == 4);
	assert(atomic_load(&counts[EC_MG24_ADAPTER_POLL_BUDGET]) == 1);
	poll();
	assert(!available && core_calls == 5);
	assert(atomic_load(&counts[EC_MG24_ADAPTER_POLL_BUDGET]) == 1);
}
/** @brief Preserve reset scopes, wrapping counts and non-consuming short replies. */
static void test_snapshots_and_reset(void)
{
	char output[400], repeated[400];
	size_t length;
	reset_fixture();
	atomic_store(&counts[EC_MG24_ADAPTER_RX_EVENT], UINT32_MAX);
	ec_mg24_adapter_count(EC_MG24_ADAPTER_RX_EVENT);
	assert(!atomic_load(&counts[EC_MG24_ADAPTER_RX_EVENT]));
	for (unsigned i = 0; i < EC_MG24_ADAPTER_COUNT; i++)
		atomic_store(&counts[i], UINT32_MAX);
	length = ec_mg24_adapter(output, sizeof(output));
	assert(length == 217);
	assert(ec_mg24_adapter(repeated, sizeof(repeated)) == length &&
	       !strcmp(output, repeated));
	memset(repeated, 0x5a, sizeof(repeated));
	assert(!ec_mg24_adapter(repeated, 0));
	assert(repeated[0] == 0x5a);
	assert(!ec_mg24_adapter(repeated, length));
	assert(repeated[length] == 0x5a);
	assert(ec_mg24_adapter(repeated, length + 1) == length &&
	       !strcmp(output, repeated));
	ec_mg24_adapter_count((enum ec_mg24_adapter_counter) - 1);
	ec_mg24_adapter_count(EC_MG24_ADAPTER_COUNT);
	assert(ec_mg24_adapter(repeated, sizeof(repeated)) == length &&
	       !strcmp(output, repeated));
	assert(start(12) == -1 && atomic_load(&counts[0]) == UINT32_MAX);
	test_start_result = 1;
	assert(start(11) == -1);
	for (unsigned i = 0; i < EC_MG24_ADAPTER_COUNT; i++)
		assert(!atomic_load(&counts[i]));
}
/** @brief Keep each named output field mapped to its own counter. */
static void test_distinct_snapshot_fields(void)
{
	char output[400];
	const char expected[] =
		"OK rx_event=1 hold_ok=2 copy_calls=3 release_ok=4 "
		"release_fail=5 core_calls=6 poll_budget=7 counter_bits=32 "
		"counter_mode=wrapping scope=last_start_attempt";

	ec_mg24_adapter_reset();
	for (unsigned i = 0; i < EC_MG24_ADAPTER_COUNT; i++)
		for (unsigned n = 0; n <= i; n++)
			ec_mg24_adapter_count((enum ec_mg24_adapter_counter)i);
	assert(ec_mg24_adapter(output, sizeof(output)) == strlen(expected));
	assert(!strcmp(output, expected));
}

/** @brief Run actual adapter callback paths against bounded SDK mocks.
 * @return Zero when every diagnostic and unchanged aggregate assertion passes.
 */
int main(void)
{
	test_callbacks_and_release();
	test_invalid_packets_and_budget();
	test_snapshots_and_reset();
	test_distinct_snapshot_fields();
#ifdef EC_RF_TRACE_DIAGNOSTICS
	char output[640];
	reset_fixture();
	event(radio, RAIL_EVENT_RX_SYNC1_DETECT | RAIL_EVENT_RX_FRAME_ERROR);
	poll();
	assert(stop() == 0);
	assert(ec_mg24_trace_command("RFTRACE EVENT 0", output,
				     sizeof(output)));
	assert(strstr(output, "mask=0000000100000004"));
	assert(ec_mg24_trace_command("RFTRACE ANCHOR 0", output,
				     sizeof(output)));
	assert(strstr(output,
		      "packet_ticks=4294967245 time_position=3 time_valid=1"));
	assert(strstr(output, "air_bytes=6 len=5"));
	assert(strstr(output, "end_ticks=25 end_valid=1 end_status=0"));
	reset_fixture();
	end_fail = 1;
	poll();
	assert(core_calls == 1 && !aggregate_drops);
	assert(stop() == 0);
	assert(ec_mg24_trace_command("RFTRACE ANCHOR 0", output,
				     sizeof(output)));
	assert(strstr(output, "end_ticks=0 end_valid=0 end_status=1"));
	reset_fixture();
	missing_time = 1;
	poll();
	assert(core_calls == 1 && !aggregate_drops);
	assert(stop() == 0);
	assert(ec_mg24_trace_command("RFTRACE ANCHOR 0", output,
				     sizeof(output)));
	assert(strstr(output, "time_position=0 time_valid=0"));
	assert(strstr(output, "end_ticks=0 end_valid=0 end_status=4294967295"));
	assert(start(26) == -1);
	assert(send(NULL, 0, 0) == -1);
#endif
	puts("mg24 adapter tests passed");
	return 0;
}
