/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef EC_EXPERIMENTAL_TRANSPORT_H
#define EC_EXPERIMENTAL_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#define EC_EXPERIMENTAL_LINE 320

struct ec_experimental {
	char line[EC_EXPERIMENTAL_LINE];
	size_t used;
	unsigned discard;
	uint32_t malformed;
	const char *board;
	const char *build;
	unsigned baud;
	/* Opt in only when the RF hook implements idle RFADAPTER handoff snapshots. */
	unsigned adapter_diagnostics;
	/* Optional RF-only diagnostic handler: command -> bounded response body. */
	size_t (*radio_command)(const char *command, char *out,
				size_t capacity);
};

/** @brief Initialize a permanently disarmed diagnostic parser.
 * @param state Parser storage, cleared by this call.
 * @param board Static exact-board token, 1..63 ASCII letters/digits/._-.
 * @param build Static source/build token with the same bounds as board.
 * @param baud Reported application transport rate; invalid tokens disable replies.
 */
void ec_experimental_init(struct ec_experimental *state, const char *board,
			  const char *build, unsigned baud);

/** @brief Consume one byte; an optional hook admits bounded synthetic RF commands.
 * @param state Initialized parser; no persistent state is changed.
 * @param byte Next received byte.
 * @param out Reply storage, used only when a complete valid request arrives.
 * @param capacity Reply storage size; truncated replies are suppressed.
 * @return Complete reply length, or zero for no reply.
 */
size_t ec_experimental_byte(struct ec_experimental *state, uint8_t byte,
			    char *out, size_t capacity);

#endif
