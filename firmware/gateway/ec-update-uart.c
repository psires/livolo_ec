#include "contiki.h"
#include "ec-update-uart.h"
#include "ec-protocol.h"
#include "ec-runtime.h"
#include "../update/ec-update.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static uint8_t manifest[EC_MANIFEST_SIZE];
static char token[17];
static unsigned header_bytes, reboot_pending;
static clock_time_t trial_started;

/**
 * @brief Restart the device to activate firmware or recover from a trial
 *   timeout.
 */
extern void ec_update_reset(void);

int ec_update_pending(void)
{
	return ec_update_receiving() || reboot_pending ||
	       ec_update_state() == EC_READY;
}

/**
 * @brief Parse an unsigned decimal update offset without overflow.
 *
 * @param s NUL-terminated decimal text.
 * @param n Parsed value, unchanged on failure.
 *
 * @return One for a valid value, otherwise zero.
 */
static int decimal(const char *s, uint32_t *n)
{
	uint32_t v = 0;

	if (!*s)
		return 0;
	for (; *s; s++) {
		if (*s < '0' || *s > '9' || v > (0xffffffffu - (*s - '0')) / 10)
			return 0;
		v = v * 10 + (*s - '0');
	}
	*n = v;
	return 1;
}

int ec_update_command(unsigned n, char **t, char *out, size_t cap)
{
	if (strncmp(t[1], "UPDATE_", 7))
		return 0;
	strcpy(out, "ERR UPDATE_SYNTAX");
	if (n == 2 && !strcmp(t[1], "UPDATE_STATUS")) {
		char public_key[65], candidate[129], current[129];

		ec_hex(ec_update_public_key, 32, public_key);
		ec_hex(ec_flash_read(EC_CANDIDATE_META + 40, 64), 64,
		       candidate);
		ec_hex(ec_flash_read(EC_CURRENT_META + 40, 64), 64, current);
		snprintf(out, cap,
			 "OK state=%08lx received=%lu receiving=%d trial=%d "
			 "key=%s candidate_sha512=%s current_sha512=%s",
			 (unsigned long)ec_update_state(),
			 (unsigned long)ec_update_received(),
			 ec_update_receiving(), ec_update_state() == EC_TRIAL,
			 public_key, candidate, current);
		return 1;
	}
	if (n == 2 && !strcmp(t[1], "UPDATE_CONFIRM")) {
		strcpy(out, ec_update_confirm() ? "ERR UPDATE_CONFIRM"
						: "OK CONFIRMED");
		return 1;
	}
	if (n == 2 && !strcmp(t[1], "UPDATE_ABORT")) {
		if (ec_update_state() == EC_READY) {
			strcpy(out, "ERR ALREADY_COMMITTED");
			return 1;
		}
		ec_update_abort();
		header_bytes = 0;
		memset(token, 0, sizeof(token));
		strcpy(out, "OK ABORTED");
		return 1;
	}
	if (n == 2 && ((!strcmp(t[1], "UPDATE_ACTIVATE") &&
			ec_update_state() == EC_READY) ||
		       !strcmp(t[1], "UPDATE_REBOOT"))) {
		reboot_pending = 1;
		strcpy(out, "OK REBOOTING");
		return 1;
	}
	if (n == 5 && !strcmp(t[1], "UPDATE_HEADER")) {
		uint8_t nonce[8], part[96];
		uint32_t offset;

		if (ec_update_pending() || ec_unhex(t[2], nonce, 8) != 8 ||
		    !decimal(t[3], &offset) || (offset != 0 && offset != 96) ||
		    ec_unhex(t[4], part, 96) != 96)
			return 1;
		if (!offset) {
			memcpy(token, t[2], 16);
			token[16] = 0;
			header_bytes = 0;
		}
		if (strcmp(token, t[2]) || offset != header_bytes)
			return 1;
		memcpy(manifest + offset, part, 96);
		header_bytes += 96;
		strcpy(out, "OK HEADER");
		return 1;
	}
	if (n < 3 || strlen(t[2]) != 16 || strcmp(token, t[2])) {
		strcpy(out, "ERR UPDATE_TOKEN");
		return 1;
	}
	if (n == 3 && !strcmp(t[1], "UPDATE_BEGIN")) {
		strcpy(out, header_bytes != 192 || ec_update_begin(manifest)
				    ? "ERR UPDATE_IMAGE"
				    : "OK BEGIN");
	} else if (n == 5 && !strcmp(t[1], "UPDATE_DATA")) {
		uint8_t data[96];
		uint32_t offset;
		int len = ec_unhex(t[4], data, sizeof(data));

		if (!decimal(t[3], &offset) || len <= 0 ||
		    ec_update_write(offset, data, len)) {
			strcpy(out, "ERR UPDATE_DATA");
		} else {
			snprintf(out, cap, "OK DATA received=%lu",
				 (unsigned long)ec_update_received());
		}
	} else if (n == 3 && !strcmp(t[1], "UPDATE_COMMIT")) {
		if (ec_update_finish())
			strcpy(out, "ERR UPDATE_COMMIT");
		else
			strcpy(out, "OK COMMITTED");
	}
	return 1;
}

void ec_update_init(void)
{
	trial_started = clock_time();
}

void ec_update_poll(void)
{
	if ((reboot_pending && ec_uart_free() == 4095) ||
	    (ec_update_state() == EC_TRIAL &&
	     (clock_time_t)(clock_time() - trial_started) >= 60 * CLOCK_SECOND))
		ec_update_reset();
	ec_update_watchdog_feed();
}
