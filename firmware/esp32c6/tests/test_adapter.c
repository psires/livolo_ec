/* SPDX-License-Identifier: BSD-2-Clause */
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned test_clock_calls, test_channel, test_releases, core_calls,
	aggregate_drops, tx_calls;
static int test_start_result, test_release_result;
#include "../main/adapter.c"
#include "../main/radio.c"

/** @brief Observe a valid callback handoff without a real portable queue.
 * @param data Borrowed MAC bytes from the test callback frame.
 * @param length Expected MAC length, excluding FCS.
 * @param included Whether original FCS is present, always false here.
 * @param rssi Mock callback signal level in dBm.
 * @param lqi Mock callback quality.
 * @param ticks Existing callback timestamp in microseconds.
 */
void ec_rf_receive(const uint8_t *data, size_t length, unsigned included,
		   int rssi, unsigned lqi, uint32_t ticks)
{
	(void)data;
	assert(length == 3 && !included && rssi == -42 && lqi == 10 &&
	       ticks == 123);
	core_calls++;
}
/** @brief Retain malformed-callback aggregate drop behavior. */
void ec_rf_drop(void)
{
	aggregate_drops++;
}
/** @brief Observe terminal TX callback delivery.
 * @param success One for completed transmission, zero for failure.
 */
void ec_rf_tx_complete(unsigned success)
{
	(void)success;
	tx_calls++;
}
/** @brief Stub the unrelated board health callback used in the ops table.
 * @param out Unused response buffer.
 * @param capacity Unused response capacity in bytes.
 * @return Zero because this fixture tests adapter diagnostics only.
 */
size_t ec_c6_health(char *out, size_t capacity)
{
	(void)out;
	(void)capacity;
	return 0;
}
/** @brief Reset only the fixture and adapter counters before a branch test. */
static void reset_fixture(void)
{
	ec_c6_adapter_reset();
	test_clock_calls = test_channel = test_releases = core_calls =
		aggregate_drops = tx_calls = 0;
	test_start_result = test_release_result = 0;
	initialized = 1;
}
/** @brief Verify SFD-only dispatch adds no clock calls or portable receive events. */
static void test_sfd_and_valid_receive(void)
{
	uint8_t frame[8] = { 5 };
	esp_ieee802154_frame_info_t info = { .rssi = -42, .lqi = 10 };
	reset_fixture();
	esp_ieee802154_receive_sfd_done();
	assert(atomic_load(&counts[EC_C6_ADAPTER_SFD_EVENT]) == 1 &&
	       !test_clock_calls && !core_calls);
	esp_ieee802154_receive_done(frame, &info);
	assert(test_clock_calls == 1 && core_calls == 1 && test_releases == 1 &&
	       !aggregate_drops);
	assert(atomic_load(&counts[EC_C6_ADAPTER_RX_DONE]) == 1);
	assert(atomic_load(&counts[EC_C6_ADAPTER_CORE_CALLS]) == 1);
	assert(atomic_load(&counts[EC_C6_ADAPTER_RX_RELEASE_OK]) == 1);
	test_release_result = 1;
	esp_ieee802154_receive_done(frame, &info);
	assert(test_releases == 2 && core_calls == 2 && !aggregate_drops);
	assert(atomic_load(&counts[EC_C6_ADAPTER_RX_RELEASE_FAIL]) == 1);
}
/** @brief Preserve each malformed callback drop and release only nonnull frames. */
static void test_invalid_and_ack_release(void)
{
	uint8_t frame[8] = { 5 };
	esp_ieee802154_frame_info_t info = { .rssi = -42, .lqi = 10 };
	reset_fixture();
	esp_ieee802154_receive_done(NULL, &info);
	esp_ieee802154_receive_done(frame, NULL);
	frame[0] = 2;
	esp_ieee802154_receive_done(frame, &info);
	frame[0] = 128;
	esp_ieee802154_receive_done(frame, &info);
	assert(aggregate_drops == 4 && test_releases == 3 && !core_calls &&
	       !test_clock_calls);
	assert(atomic_load(&counts[EC_C6_ADAPTER_RX_INVALID]) == 4);
	assert(atomic_load(&counts[EC_C6_ADAPTER_RX_NULL]) == 1);
	esp_ieee802154_transmit_done(frame, NULL, NULL);
	assert(test_releases == 3);
	esp_ieee802154_transmit_done(frame, frame, &info);
	assert(test_releases == 4);
	test_release_result = 1;
	esp_ieee802154_transmit_done(frame, frame, &info);
	assert(test_releases == 5 && tx_calls == 3 && aggregate_drops == 4);
	assert(atomic_load(&counts[EC_C6_ADAPTER_ACK_RELEASE_OK]) == 1);
	assert(atomic_load(&counts[EC_C6_ADAPTER_ACK_RELEASE_FAIL]) == 1);
}
/** @brief Verify pure snapshots, uint32 wrapping, short buffers and failed-start reset. */
static void test_snapshots_and_reset(void)
{
	char output[400], repeated[400];
	size_t length;
	reset_fixture();
	atomic_store(&counts[EC_C6_ADAPTER_SFD_EVENT], UINT32_MAX);
	ec_c6_adapter_count(EC_C6_ADAPTER_SFD_EVENT);
	assert(!atomic_load(&counts[0]));
	for (unsigned i = 0; i < EC_C6_ADAPTER_COUNT; i++)
		atomic_store(&counts[i], UINT32_MAX);
	length = ec_c6_adapter(output, sizeof(output));
	assert(length == 274);
	assert(ec_c6_adapter(repeated, sizeof(repeated)) == length &&
	       !strcmp(output, repeated));
	memset(repeated, 0x5a, sizeof(repeated));
	assert(!ec_c6_adapter(repeated, 0));
	assert(repeated[0] == 0x5a);
	assert(!ec_c6_adapter(repeated, length));
	assert(repeated[length] == 0x5a);
	assert(ec_c6_adapter(repeated, length + 1) == length &&
	       !strcmp(output, repeated));
	ec_c6_adapter_count((enum ec_c6_adapter_counter) - 1);
	ec_c6_adapter_count(EC_C6_ADAPTER_COUNT);
	assert(ec_c6_adapter(repeated, sizeof(repeated)) == length &&
	       !strcmp(output, repeated));
	assert(!test_clock_calls && !test_releases && !aggregate_drops);
	assert(start(12) == -1 && atomic_load(&counts[0]) == UINT32_MAX);
	test_start_result = 1;
	assert(start(11) == -1);
	for (unsigned i = 0; i < EC_C6_ADAPTER_COUNT; i++)
		assert(!atomic_load(&counts[i]));
}
/** @brief Keep each named output field mapped to its own counter. */
static void test_distinct_snapshot_fields(void)
{
	char output[400];
	const char expected[] =
		"OK sfd_event=1 rx_done=2 rx_invalid=3 rx_null=4 "
		"rx_release_ok=5 rx_release_fail=6 ack_release_ok=7 "
		"ack_release_fail=8 core_calls=9 counter_bits=32 "
		"counter_mode=wrapping scope=last_start_attempt";

	ec_c6_adapter_reset();
	for (unsigned i = 0; i < EC_C6_ADAPTER_COUNT; i++)
		for (unsigned n = 0; n <= i; n++)
			ec_c6_adapter_count((enum ec_c6_adapter_counter)i);
	assert(ec_c6_adapter(output, sizeof(output)) == strlen(expected));
	assert(!strcmp(output, expected));
}

/** @brief Run actual C6 callbacks against deterministic SDK return-value mocks.
 * @return Zero when handoff, scope and buffer-boundary assertions all pass.
 */
int main(void)
{
	test_sfd_and_valid_receive();
	test_invalid_and_ack_release();
	test_snapshots_and_reset();
	test_distinct_snapshot_fields();
	puts("c6 adapter tests passed");
	return 0;
}
