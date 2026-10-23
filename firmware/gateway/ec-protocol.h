#ifndef EC_PROTOCOL_H
#define EC_PROTOCOL_H

#include <stdint.h>
#include <stddef.h>

typedef struct {
	uint8_t gateway[4], device[4], tag;
} ec_peer_t;

/**
 * @brief Calculate the CRC used to validate EC payload integrity.
 *
 * @param data Payload bytes to checksum.
 * @param len Number of payload bytes.
 *
 * @return CRC-16/CCITT with initial value 0xffff.
 */
uint16_t ec_crc16(const uint8_t *data, size_t len);

/**
 * @brief Create an acknowledgement for an eligible report from a configured
 *   peer.
 *
 * @param peer Gateway, device and network identities to accept.
 * @param rx Received frame bytes, without the radio FCS.
 * @param len Number of received bytes.
 * @param out Destination for the acknowledgement; must not overlap rx.
 * @param capacity Available bytes in out.
 *
 * @return Acknowledgement length, or zero when the frame cannot be
 *   acknowledged.
 */
size_t ec_make_ack(const ec_peer_t *peer, const uint8_t *rx, size_t len,
		   uint8_t *out, size_t capacity);

/**
 * @brief Check whether a frame satisfies the configured peer transmit policy.
 *
 * @param peer Gateway, device and network identities to permit.
 * @param raw Candidate frame bytes, without the radio FCS.
 * @param len Number of candidate bytes.
 *
 * @return Nonzero for an allowed frame, otherwise zero.
 */
int ec_tx_allowed(const ec_peer_t *peer, const uint8_t *raw, size_t len);

/**
 * @brief Decode a hexadecimal string into bytes.
 *
 * @param text NUL-terminated hexadecimal text without separators.
 * @param out Destination buffer; may contain partial output on failure.
 * @param capacity Available bytes in out.
 *
 * @return Decoded byte count, or -1 for invalid text or insufficient capacity.
 */
int ec_unhex(const char *text, uint8_t *out, size_t capacity);

/**
 * @brief Render bytes as a lowercase hexadecimal string.
 *
 * @param raw Bytes to encode.
 * @param len Number of input bytes.
 * @param out Destination with room for 2 * len + 1 bytes, including NUL.
 */
void ec_hex(const uint8_t *raw, size_t len, char *out);

#endif
