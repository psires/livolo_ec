/* SPDX-License-Identifier: BSD-2-Clause */
#include "transport.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

/** @brief Supply a harmless RF hook for explicit capability-negotiation tests.
 * @param command Complete diagnostic request, unused by this fixture.
 * @param out Response body storage.
 * @param capacity Available bytes including NUL.
 * @return Complete fixed reply length, or zero when it does not fit.
 */
static size_t radio_reply(const char *command, char *out, size_t capacity)
{
	int length;

	(void)command;
	length = snprintf(out, capacity, "OK fixture=1");
	return length > 0 && (size_t)length < capacity ? (size_t)length : 0;
}

/** @brief Feed a complete request through the actual byte parser.
 * @param state Parser to exercise.
 * @param input NUL-terminated request bytes.
 * @param output At least 640 bytes of reply storage.
 * @return Sum of emitted reply bytes.
 */
static size_t feed(struct ec_experimental *state, const char *input,
		   char *output)
{
	size_t total = 0;

	while (*input)
		total += ec_experimental_byte(state, *input++, output, 640);
	return total;
}

/** @brief Check fail-closed capabilities, malformed framing and resynchronizing. */
int main(void)
{
	struct ec_experimental state;
	char output[640];
	char overflow[400];
	const char *mutating[] = {
		"2 NETWORK 12345678 b1 123456789abcdef0\n",
		"3 ARM 123456789abcdef0 60\n",
		"4 SEND 123456789abcdef0 0188\n",
		"5 PEER CLEAR\n",
		"6 ACK 123456789abcdef0 1\n",
		"7 PAIR\n",
		"8 UPDATE\n",
		"9 RESET\n",
		"10 BOOTLOADER\n",
	};

	ec_experimental_init(&state, "seeed-xiao-mg24", "test", 115200);
	assert(feed(&state, "1 PING\n", output));
	assert(!strcmp(output, "RSP 1 OK EC-GATEWAY 3\n"));
	assert(feed(&state, "4294967295 CAPS\n", output));
	assert(strstr(output, "peers=0 ack_mask=00 max_raw=0"));
	assert(strstr(output, "lease_max=0 session_bytes=0"));
	assert(strstr(output,
		      "update=none experimental=1 gateway=0 radio_tx=0"));
	assert(!strstr(output, "queue_diag="));
	assert(!strstr(output, "adapter_diag="));
	state.adapter_diagnostics = 1;
	assert(feed(&state, "1 CAPS\n", output));
	assert(!strstr(output, "adapter_diag="));
	state.radio_command = radio_reply;
	assert(feed(&state, "1 CAPS\n", output));
	assert(strstr(output, "adapter_diag=handoff_v1"));
	assert(feed(&state, "1 RFADAPTER\n", output));
	assert(!strcmp(output, "RSP 1 OK fixture=1\n"));
	state.adapter_diagnostics = 0;
	assert(feed(&state, "1 CAPS\n", output));
	assert(!strstr(output, "adapter_diag="));
	ec_experimental_init(&state, "seeed-xiao-mg24", "test", 115200);
	for (size_t i = 0; i < sizeof(mutating) / sizeof(mutating[0]); i++) {
		assert(feed(&state, mutating[i], output));
		assert(strstr(output, "ERR UNSUPPORTED"));
	}
	assert(!feed(&state, "0 PING\n", output));
	assert(!feed(&state, "4294967296 PING\n", output));
	assert(!feed(&state, "junk 12 PING\n", output));
	memset(overflow, 'x', sizeof(overflow) - 1);
	overflow[sizeof(overflow) - 1] = '\0';
	assert(!feed(&state, overflow, output));
	assert(!feed(&state, "12 PING\n", output));
	assert(feed(&state, "13 PING\n", output));
	assert(!strcmp(output, "RSP 13 OK EC-GATEWAY 3\n"));
	ec_experimental_byte(&state, 0, output, sizeof(output));
	assert(!feed(&state, "14 PING\n", output));
	assert(feed(&state, "15 STATUS\n", output));
	assert(strstr(output, "configured=0 armed=0 peers=0 radio_tx=0"));
	assert(!feed(&state, "16 PING", output));
	assert(!ec_experimental_byte(&state, '\n', output, 4));
	ec_experimental_init(&state, overflow, "test", 115200);
	assert(!feed(&state, "1 VERSION\n", output));
	ec_experimental_init(&state, "board", "space injection=1", 115200);
	assert(!feed(&state, "1 CAPS\n", output));
	puts("experimental transport: PASS");
	return 0;
}
