#ifndef EC_PEERS_H
#define EC_PEERS_H

#include "ec-protocol.h"

#define EC_PEER_CAPACITY 256
#define EC_ACK_MASK 0x1f

typedef struct {
	uint8_t device[4], ack_mask, used;
} ec_slot_t;

typedef struct {
	uint8_t gateway[4], tag, configured;
	uint16_t count;
	ec_slot_t slots[EC_PEER_CAPACITY];
} ec_network_t;

/**
 * @brief Reject reserved all-zero and broadcast identities.
 *
 * @param id Four-byte identity to validate.
 *
 * @return Nonzero for a usable identity, otherwise zero.
 */
int ec_identity_valid(const uint8_t *id);

/**
 * @brief Configure a network identity and clear all previous peer rules.
 *
 * @param net Network state to replace; unchanged on invalid input.
 * @param gateway Four-byte gateway identity; may alias net->gateway.
 * @param tag Network tag in the range 1 through 254.
 *
 * @return One on success, or zero for an invalid identity or tag.
 */
int ec_network_set(ec_network_t *net, const uint8_t *gateway, uint8_t tag);

/**
 * @brief Assign a unique device and acknowledgement policy to a peer slot.
 *
 * @param net Configured network to update.
 * @param slot Zero-based peer slot to create or replace.
 * @param device Four-byte device identity.
 * @param mask Acknowledgement policy bits from EC_ACK_MASK.
 *
 * @return One on success, zero for invalid input, or -1 for a duplicate device.
 */
int ec_peer_set(ec_network_t *net, unsigned slot, const uint8_t *device,
		unsigned mask);

/**
 * @brief Remove a peer rule from a configured network.
 *
 * @param net Network whose peer rule is removed.
 * @param slot Zero-based slot to clear; an empty slot is accepted.
 *
 * @return One on success, or zero for an unconfigured network or invalid slot.
 */
int ec_peer_del(ec_network_t *net, unsigned slot);

/**
 * @brief Remove all peer rules while preserving the network identity.
 *
 * @param net Network whose peer table and count are cleared.
 */
void ec_peers_clear(ec_network_t *net);

/**
 * @brief Create a reply only when the network and peer acknowledgement rules
 *   permit it.
 *
 * @param net Configured network and peer acknowledgement policies.
 * @param raw Received frame bytes, without the radio FCS.
 * @param len Number of received bytes.
 * @param out Reply buffer; must not overlap raw.
 * @param capacity Available bytes in out.
 *
 * @return Reply length, or zero when no reply is permitted or fits.
 */
size_t ec_network_ack(const ec_network_t *net, const uint8_t *raw, size_t len,
		      uint8_t *out, size_t capacity);

/**
 * @brief Check a frame against network membership and wire-level transmit
 *   policy.
 *
 * @param net Configured network and permitted peers.
 * @param raw Candidate frame bytes, without the radio FCS.
 * @param len Number of candidate bytes.
 *
 * @return Nonzero when transmission is permitted, otherwise zero.
 */
int ec_network_tx_allowed(const ec_network_t *net, const uint8_t *raw,
			  size_t len);

#endif
