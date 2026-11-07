/* SPDX-License-Identifier: BSD-2-Clause */
#include "transport.h"
#include "../version.h"
#include <stdio.h>
#include <string.h>

/** @brief Validate a bounded whitespace-free build or board token.
 * @param token Static token supplied by the board adapter, possibly NULL.
 * @return Nonzero only for 1 through 63 safe ASCII identifier characters.
 */
static int valid_token(const char *token)
{
	if (!token || !*token)
		return 0;
	for (size_t i = 0; i < 64; i++) {
		char c = token[i];

		if (!c)
			return 1;
		if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
		      (c >= '0' && c <= '9') || c == '-' || c == '_' ||
		      c == '.'))
			return 0;
	}
	return 0;
}

void ec_experimental_init(struct ec_experimental *state, const char *board,
			  const char *build, unsigned baud)
{
	memset(state, 0, sizeof(*state));
	state->board = valid_token(board) ? board : NULL;
	state->build = valid_token(build) ? build : NULL;
	state->baud = baud;
}

/** @brief Format the response to one terminated diagnostic request.
 * @param state Parser holding the complete ASCII line.
 * @param out Reply storage.
 * @param capacity Available reply bytes including terminator.
 * @return Untruncated reply length, or zero for malformed input.
 */
static size_t reply(struct ec_experimental *state, char *out, size_t capacity)
{
	const char *cursor = state->line;
	uint32_t id = 0;
	char body[512];
	int length;

	if (!state->board || !state->build || *cursor < '0' || *cursor > '9')
		goto malformed;
	while (*cursor >= '0' && *cursor <= '9') {
		unsigned digit = *cursor++ - '0';

		if (id > (UINT32_MAX - digit) / 10)
			goto malformed;
		id = id * 10 + digit;
	}
	if (!id || *cursor++ != ' ')
		goto malformed;
	if (!strcmp(cursor, "PING")) {
		strcpy(body, "OK EC-GATEWAY 3");
	} else if (!strcmp(cursor, "VERSION")) {
		length = snprintf(body, sizeof(body),
				  "OK fw_version=" EC_VERSION
				  " board=%s proto=3 "
				  "update=none experimental=1 build=%s",
				  state->board, state->build);
		if (length < 0 || (size_t)length >= sizeof(body))
			return 0;
	} else if (!strcmp(cursor, "CAPS")) {
		length = snprintf(
			body, sizeof(body),
			"OK proto=3 peers=0 ack_mask=00 max_raw=0 max_line=319 "
			"command_slots=0 request_cache=0 lease_max=0 "
			"session_bytes=0 channel=11 baud=%u board=%s "
			"fw_version=" EC_VERSION " update=none experimental=1 "
			"gateway=0 radio_tx=%s%s build=%s",
			state->baud, state->board,
			state->radio_command ? "synthetic_only" : "0",
			state->radio_command
				? " rf_diag=synthetic_v1 rx_diag=passive_v1"
				: "",
			state->build);
		if (length < 0 || (size_t)length >= sizeof(body))
			return 0;
	} else if (!strcmp(cursor, "STATUS")) {
		snprintf(body, sizeof(body),
			 "OK configured=0 armed=0 peers=0 radio_tx=%s "
			 "experimental=1 malformed=%lu",
			 state->radio_command ? "synthetic_only" : "0",
			 (unsigned long)state->malformed);
	} else if (state->radio_command && !strncmp(cursor, "RF", 2)) {
		if (!state->radio_command(cursor, body, sizeof(body)))
			return 0;
	} else {
		strcpy(body, "ERR UNSUPPORTED experimental=1");
	}
	length = snprintf(out, capacity, "RSP %lu %s\n", (unsigned long)id,
			  body);
	return length > 0 && (size_t)length < capacity ? (size_t)length : 0;

malformed:
	state->malformed++;
	return 0;
}

size_t ec_experimental_byte(struct ec_experimental *state, uint8_t byte,
			    char *out, size_t capacity)
{
	size_t length = 0;

	if (byte == '\n') {
		if (!state->discard && state->used) {
			state->line[state->used] = '\0';
			length = reply(state, out, capacity);
		}
		state->used = 0;
		state->discard = 0;
		return length;
	}
	if (state->discard)
		return 0;
	if (byte < 32 || byte > 126 || state->used == sizeof(state->line) - 1) {
		state->discard = 1;
		state->malformed++;
		return 0;
	}
	state->line[state->used++] = (char)byte;
	return 0;
}
