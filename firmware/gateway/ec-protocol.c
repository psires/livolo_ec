/* Pure protocol functions shared with host-side capture-vector tests. */
#include "ec-protocol.h"
#include <string.h>

/**
 * @brief Calculate the EC CRC-16/CCITT payload checksum.
 * @param p Borrowed payload buffer with at least n readable bytes.
 * @param n Number of payload bytes.
 * @return CRC-16/CCITT with initial value 0xffff.
 */
uint16_t ec_crc16(const uint8_t *p, size_t n)
{
	uint16_t crc = 0xffff;
	unsigned b;

	while (n--) {
		crc ^= (uint16_t)*p++ << 8;
		for (b = 0; b < 8; b++) {
			crc = (uint16_t)((crc << 1) ^
					 ((crc & 0x8000) ? 0x1021 : 0));
		}
	}
	return crc;
}

/**
 * @brief Calculate the additive checksum used by EC replies.
 *
 * @param p Payload bytes to checksum.
 * @param n Number of payload bytes.
 *
 * @return Sum of the payload bytes modulo 256.
 */
static uint8_t sum8(const uint8_t *p, size_t n)
{
	uint8_t sum = 0;

	while (n--)
		sum += *p++;
	return sum;
}

size_t ec_make_ack(const ec_peer_t *peer, const uint8_t *rx, size_t len,
		   uint8_t *out, size_t capacity)
{
	static const uint8_t broadcast[4] = { 255, 255, 255, 255 };
	uint16_t crc;

	if (len != 39 || capacity < 41 || rx[0] != 1 || rx[1] != 0x88 ||
	    rx[21] != 0x9e || rx[19] != peer->tag ||
	    memcmp(rx + 7, peer->device, 4) || memcmp(rx + 23, peer->device, 4))
		return 0;
	/*
	 * The paired spare broadcasts state reports when its direct route is
	 * lost. Reply only to this observed opcode from our exact peer and tag.
	 */
	if (memcmp(rx + 3, peer->gateway, 4) &&
	    !(rx[22] == 0x05 && !memcmp(rx + 3, broadcast, 4)))
		return 0;
	crc = ec_crc16(rx + 21, 16);
	if (rx[37] != (crc >> 8) || rx[38] != (crc & 255))
		return 0;
	/* No unsolicited join admission, announcement replies, or ACK-of-ACK loops. */
	if (rx[22] != 0x05 && rx[22] != 0x15 && rx[22] != 0xa0 &&
	    rx[22] != 0x08)
		return 0;
	memset(out, 0, 41);
	out[0] = 1;
	out[1] = 0x88;
	out[2] = rx[2];
	memcpy(out + 3, peer->device, 4);
	memcpy(out + 7, peer->gateway, 4);
	out[19] = peer->tag;
	out[20] = 0x0e;
	out[21] = 0x8c;
	out[22] = rx[22] == 0x08 ? 0x08 : 0xfa;
	memcpy(out + 23, rx + 23, 6);
	if (rx[22] != 0x08) {
		out[29] = rx[22];
		memcpy(out + 30, rx + 29, 7);
	}
	out[37] = sum8(out + 21, 16);
	/* Zero timing trailer was exercised during local admission and control. */
	return 41;
}

int ec_tx_allowed(const ec_peer_t *peer, const uint8_t *raw, size_t len)
{
	static const uint8_t broadcast[4] = { 255, 255, 255, 255 };

	if (len < 11 || len > 125 || raw[0] != 1 || raw[1] != 0x88 ||
	    memcmp(raw + 7, peer->gateway, 4))
		return 0;
	if (memcmp(raw + 3, peer->device, 4) &&
	    memcmp(raw + 3, peer->gateway, 4) && memcmp(raw + 3, broadcast, 4))
		return 0;
	if (len == 41 && raw[21] == 0x8c)
		return raw[19] == peer->tag && raw[37] == sum8(raw + 21, 16);
	/* Non-EC diagnostic frames must be addressed to our own identity. */
	return !memcmp(raw + 3, peer->gateway, 4) && len < 39;
}

/**
 * @brief Interpret a single hexadecimal digit.
 *
 * @param c Character to decode.
 *
 * @return Digit value from zero through fifteen, or -1 for an invalid digit.
 */
static int nibble(char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}

int ec_unhex(const char *text, uint8_t *out, size_t capacity)
{
	size_t n = strlen(text);
	size_t i;

	if ((n & 1) || n / 2 > capacity)
		return -1;
	for (i = 0; i < n / 2; i++) {
		int a = nibble(text[2 * i]);
		int b = nibble(text[2 * i + 1]);

		if (a < 0 || b < 0)
			return -1;
		out[i] = (uint8_t)((a << 4) | b);
	}
	return (int)(n / 2);
}

void ec_hex(const uint8_t *raw, size_t len, char *out)
{
	static const char digits[] = "0123456789abcdef";
	size_t i;

	for (i = 0; i < len; i++) {
		out[2 * i] = digits[raw[i] >> 4];
		out[2 * i + 1] = digits[raw[i] & 15];
	}
	out[2 * len] = 0;
}
