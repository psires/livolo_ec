#ifndef EC_SNIFFER_WIRE_H
#define EC_SNIFFER_WIRE_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define SNIFFER_HEADER 20u
#define SNIFFER_MAX_BODY 129u
#define SNIFFER_MAX_RECORD (SNIFFER_HEADER + SNIFFER_MAX_BODY + 4u)

/** @brief Store a little-endian integer without alignment requirements.
 * @param dst Destination with at least count writable bytes.
 * @param value Unsigned integer to encode.
 * @param count Number of bytes, 1 through 8. */
static inline void sniff_put(uint8_t *dst, uint64_t value, unsigned count)
{
	for (unsigned i = 0; i < count; i++)
		dst[i] = value >> (i * 8);
}

/** @brief Compute the standard reflected CRC32 used by Python zlib.
 * @param data Borrowed input bytes.
 * @param length Number of readable bytes.
 * @return CRC32 with initial/final inversion. */
static inline uint32_t sniff_crc(const uint8_t *data, size_t length)
{
	uint32_t crc = 0xffffffffu;

	for (size_t i = 0; i < length; i++) {
		crc ^= data[i];
		for (unsigned bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
	}
	return ~crc;
}

/** @brief Encode one bounded binary record; all integer fields are little endian.
 * @param out Destination with SNIFFER_MAX_RECORD writable bytes.
 * @param kind One for RX, two for status.
 * @param sequence Wrapping record sequence, including rejected UART records.
 * @param timestamp Extended radio microseconds; RX uses sync-word end.
 * @param body Borrowed payload, at most SNIFFER_MAX_BODY bytes.
 * @param length Payload byte count.
 * @return Complete record length, or zero for oversized payload. */
static inline size_t sniff_record(uint8_t *out, unsigned kind,
				  uint32_t sequence, uint64_t timestamp,
				  const void *body, size_t length)
{
	if (length > SNIFFER_MAX_BODY)
		return 0;
	memcpy(out, "ESN1", 4);
	sniff_put(out + 4, length, 2);
	out[6] = kind;
	out[7] = 1; /* Radio sync-word-end timestamps; FCS omitted. */
	sniff_put(out + 8, sequence, 4);
	sniff_put(out + 12, timestamp, 8);
	memcpy(out + SNIFFER_HEADER, body, length);
	sniff_put(out + SNIFFER_HEADER + length,
		  sniff_crc(out, SNIFFER_HEADER + length), 4);
	return SNIFFER_HEADER + length + 4;
}
#endif
