/* SPDX-License-Identifier: BSD-2-Clause */
#include "rf.h"
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#define RX_SLOTS 8u
_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
	       "ISR ring requires lock-free uint atomics");
struct rx_record {
	uint8_t data[127];
	unsigned length, included, lqi;
	int rssi;
	uint32_t ticks;
};
static const struct ec_rf_ops *radio;
static struct rx_record ring[RX_SLOTS];
static atomic_uint active, busy, completion, head, tail, received, dropped;
static uint32_t nonce, started, sent_at;
static unsigned tx_started, tx_done, tx_failed, tx_sequence, event_pending;
static unsigned fault;
static unsigned channel;
static uint8_t sequences[32], tx_frame[41];

void ec_rf_setup(const struct ec_rf_ops *ops)
{
	radio = ops;
	atomic_store(&active, 0);
	atomic_store(&busy, 0);
	atomic_store(&completion, 0);
	atomic_store(&head, 0);
	atomic_store(&tail, 0);
	atomic_store(&received, 0);
	atomic_store(&dropped, 0);
	nonce = started = sent_at = 0;
	tx_started = tx_done = tx_failed = event_pending = fault = 0;
	channel = 26;
	memset(sequences, 0, sizeof(sequences));
}

/** @brief Parse a bounded unsigned token without scanf overflow.
 * @param token NUL-terminated ASCII token.
 * @param base Decimal or lowercase hexadecimal base.
 * @param result Parsed value on success.
 * @return Nonzero for a complete valid uint32 token.
 */
static int number(const char *token, unsigned base, uint32_t *result)
{
	uint32_t value = 0;

	if (!*token)
		return 0;
	while (*token) {
		unsigned digit;
		char c = *token++;

		if (c >= '0' && c <= '9')
			digit = c - '0';
		else if (base == 16 && c >= 'a' && c <= 'f')
			digit = c - 'a' + 10;
		else
			return 0;
		if (digit >= base || value > (UINT32_MAX - digit) / base)
			return 0;
		value = value * base + digit;
	}
	*result = value;
	return 1;
}

/** @brief Stop RF before releasing ownership; interrupted TX latches a fault.
 * @return Nonzero if the adapter could not establish a quiescent radio.
 */
static int stop(void)
{
	int result;

	atomic_store_explicit(&active, 0, memory_order_release);
	if (atomic_load(&busy))
		fault = 1;
	result = radio->stop();
	if (result)
		fault = 1;
	if (atomic_exchange(&busy, 0)) {
		tx_failed++;
		event_pending = 3;
	}
	return result;
}

void ec_rf_poll(void)
{
	unsigned result;
	uint32_t now;

	if (!radio)
		return;
	radio->poll();
	result = atomic_exchange_explicit(&completion, 0, memory_order_acquire);
	if (result && atomic_exchange(&busy, 0)) {
		if (result == 1)
			tx_done++;
		else
			tx_failed++;
		event_pending = result;
	}
	if (!atomic_load(&active))
		return;
	now = radio->now_us();
	if ((uint32_t)(now - started) >= 60000000u ||
	    (atomic_load(&busy) && (uint32_t)(now - sent_at) >= 20000u))
		stop();
}

size_t ec_rf_command(const char *command, char *out, size_t capacity)
{
	char copy[320], *words[7], *word;
	unsigned count = 0;
	uint32_t values[5];
	const char *response = "ERR RF_SYNTAX";
	int length;

	ec_rf_poll();
	if (!radio || strlen(command) >= sizeof(copy))
		goto done;
	strcpy(copy, command);
	word = strtok(copy, " ");
	while (word && count < 7) {
		words[count++] = word;
		word = strtok(NULL, " ");
	}
	if (!count || word)
		goto done;
	if (count == 1 && !strcmp(words[0], "RFHEALTH")) {
		if (radio->health)
			return radio->health(out, capacity);
		response = "ERR RF_HEALTH_UNSUPPORTED";
		goto done;
	}
	if (count == 1 && !strcmp(words[0], "RFSTATUS")) {
		length = snprintf(
			out, capacity,
			"OK active=%u channel=%u mode=%s power_dbm=%d "
			"auto_ack=0 retries=0 "
			"tx_started=%u tx_done=%u tx_failed=%u rx=%u "
			"dropped=%u "
			"queued=%u busy=%u fault=%u ticks_hz=1000000 "
			"timestamp=callback bad_crc=unavailable",
			atomic_load(&active), channel,
			channel == 11 ? "passive" : "synthetic",
			radio->power_dbm(), tx_started, tx_done, tx_failed,
			atomic_load(&received), atomic_load(&dropped),
			(atomic_load(&head) - atomic_load(&tail)) &
				(RX_SLOTS - 1),
			atomic_load(&busy), fault);
		return length > 0 && (size_t)length < capacity ? length : 0;
	}
	if (count == 1 && !strcmp(words[0], "RFSTOP")) {
		response = stop() ? "ERR RF_STOP" : "OK active=0";
		goto done;
	}
	if (!strcmp(words[0], "RFTX") && channel == 11) {
		response = "ERR RF_PASSIVE";
		goto done;
	}
	if (count != 2 && count != 6)
		goto done;
	if (strlen(words[1]) != 8 || !number(words[1], 16, &values[0]) ||
	    !values[0])
		goto done;
	if (count == 2 &&
	    (!strcmp(words[0], "RFSTART") || !strcmp(words[0], "RFLISTEN"))) {
		unsigned selected = !strcmp(words[0], "RFLISTEN") ? 11 : 26;

		if (fault || atomic_load(&active) || event_pending) {
			response = "ERR RF_BUSY_OR_FAULT";
			goto done;
		}
		if (radio->start(selected) || radio->power_dbm() > 0) {
			radio->stop();
			fault = 1;
			response = "ERR RF_INIT";
			goto done;
		}
		channel = selected;
		nonce = values[0];
		started = radio->now_us();
		tx_started = tx_done = tx_failed = 0;
		atomic_store(&received, 0);
		atomic_store(&dropped, 0);
		atomic_store(&head, 0);
		atomic_store(&tail, 0);
		memset(sequences, 0, sizeof(sequences));
		atomic_store_explicit(&active, 1, memory_order_release);
		response = channel == 11 ? "OK active=1 channel=11 mode=passive"
					 : "OK active=1 channel=26";
		goto done;
	}
	if (count != 6 || strcmp(words[0], "RFTX"))
		goto done;
	for (unsigned i = 1; i < 5; i++)
		if (!number(words[i + 1], 10, &values[i]))
			goto done;
	if (values[1] > 255 || (values[2] != 39 && values[2] != 41) ||
	    values[3] > 1 || values[4] > 1)
		goto done;
	if (!atomic_load(&active) || fault || nonce != values[0]) {
		response = "ERR RF_NOT_ACTIVE";
		goto done;
	}
	if (atomic_load(&busy) || event_pending || tx_started >= 64 ||
	    (tx_started && (uint32_t)(radio->now_us() - sent_at) < 100000u) ||
	    (sequences[values[1] / 8] & (1u << (values[1] % 8)))) {
		response = "ERR RF_LIMIT";
		goto done;
	}
	for (unsigned i = 0; i < values[2]; i++)
		tx_frame[i] = (i * 17 + values[1]) & 255;
	memcpy(tx_frame, "\x01\x88\x00\xef\xbe\x22\x22\xef\xbe\x11\x11XRF1",
	       15);
	tx_frame[0] |= values[4] << 5;
	tx_frame[2] = tx_frame[19] = values[1];
	for (unsigned i = 0; i < 4; i++)
		tx_frame[15 + i] = nonce >> (24 - i * 8);
	tx_frame[20] = values[2];
	tx_frame[21] = values[3];
	tx_frame[22] = values[4];
	tx_sequence = values[1];
	sequences[values[1] / 8] |= 1u << (values[1] % 8);
	sent_at = radio->now_us();
	tx_started++;
	atomic_store(&completion, 0);
	atomic_store_explicit(&busy, 1, memory_order_release);
	if (radio->send(tx_frame, values[2], values[3])) {
		atomic_store(&busy, 0);
		tx_failed++;
		event_pending = 2;
		response = "ERR RF_TX";
		goto done;
	}
	length = snprintf(out, capacity, "OK queued=1 seq=%u", tx_sequence);
	return length > 0 && (size_t)length < capacity ? length : 0;
done:
	length = snprintf(out, capacity, "%s", response);
	return length > 0 && (size_t)length < capacity ? length : 0;
}

void ec_rf_receive(const uint8_t *data, size_t length, unsigned included,
		   int rssi, unsigned lqi, uint32_t ticks)
{
	unsigned write, next;
	struct rx_record *record;

	if (!atomic_load_explicit(&active, memory_order_acquire))
		return;
	if (!length || length > 125 || included > 1) {
		ec_rf_drop();
		return;
	}
	atomic_fetch_add(&received, 1);
	write = atomic_load_explicit(&head, memory_order_relaxed);
	next = (write + 1) & (RX_SLOTS - 1);
	if (next == atomic_load_explicit(&tail, memory_order_acquire)) {
		ec_rf_drop();
		return;
	}
	record = &ring[write];
	memcpy(record->data, data, length + included * 2);
	record->length = length;
	record->included = included;
	record->rssi = rssi;
	record->lqi = lqi;
	record->ticks = ticks;
	atomic_store_explicit(&head, next, memory_order_release);
}

void ec_rf_drop(void)
{
	atomic_fetch_add_explicit(&dropped, 1, memory_order_relaxed);
}

void ec_rf_tx_complete(unsigned success)
{
	if (atomic_load_explicit(&busy, memory_order_acquire))
		atomic_store_explicit(&completion, success ? 1 : 2,
				      memory_order_release);
}

size_t ec_rf_event(char *out, size_t capacity)
{
	unsigned read;
	struct rx_record *record;
	char hex[255];
	static const char alphabet[] = "0123456789abcdef";
	int length;

	if (event_pending) {
		length = snprintf(out, capacity, "RF TX seq=%u status=%s\n",
				  tx_sequence,
				  event_pending == 1   ? "done"
				  : event_pending == 2 ? "failed"
						       : "timeout");
		if (length <= 0 || (size_t)length >= capacity)
			return 0;
		event_pending = 0;
		return length;
	}
	read = atomic_load_explicit(&tail, memory_order_relaxed);
	if (read == atomic_load_explicit(&head, memory_order_acquire))
		return 0;
	record = &ring[read];
	for (unsigned i = 0; i < record->length + record->included * 2; i++) {
		hex[i * 2] = alphabet[record->data[i] >> 4];
		hex[i * 2 + 1] = alphabet[record->data[i] & 15];
	}
	hex[(record->length + record->included * 2) * 2] = 0;
	length = snprintf(
		out, capacity,
		"RF RX len=%u fcs=%s crc=1 rssi=%d lqi=%u ticks=%lu data=%s\n",
		record->length, record->included ? "included" : "stripped",
		record->rssi, record->lqi, (unsigned long)record->ticks, hex);
	if (length <= 0 || (size_t)length >= capacity)
		return 0;
	atomic_store_explicit(&tail, (read + 1) & (RX_SLOTS - 1),
			      memory_order_release);
	return length;
}
