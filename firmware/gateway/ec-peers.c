/*
 * All state is RAM-only. Callers serialize edits and radio callbacks in
 * Contiki process context. The bounded table contains radio rules, not device
 * state.
 */
#include "ec-peers.h"
#include <string.h>

int ec_identity_valid(const uint8_t *id)
{
	return (id[0] || id[1] || id[2] || id[3]) &&
	       !(id[0] == 255 && id[1] == 255 && id[2] == 255 && id[3] == 255);
}

void ec_peers_clear(ec_network_t *net)
{
	memset(net->slots, 0, sizeof(net->slots));
	net->count = 0;
}

int ec_network_set(ec_network_t *net, const uint8_t *gateway, uint8_t tag)
{
	uint8_t copy[4];

	if (!ec_identity_valid(gateway) || !tag || tag == 255)
		return 0;
	/* Copy first, so callers may safely pass net->gateway. */
	memcpy(copy, gateway, 4);
	memset(net, 0, sizeof(*net));
	memcpy(net->gateway, copy, 4);
	net->tag = tag;
	net->configured = 1;
	return 1;
}

/**
 * @brief Locate the peer rule associated with a device identity.
 *
 * @param net Network whose peer table is searched.
 * @param device Four-byte device identity to find.
 *
 * @return Matching slot index, or -1 when the device is absent.
 */
static int find(const ec_network_t *net, const uint8_t *device)
{
	unsigned i;

	for (i = 0; i < EC_PEER_CAPACITY; i++) {
		if (net->slots[i].used &&
		    !memcmp(net->slots[i].device, device, 4))
			return (int)i;
	}
	return -1;
}

int ec_peer_set(ec_network_t *net, unsigned slot, const uint8_t *device,
		unsigned mask)
{
	ec_slot_t *p;
	int existing;

	if (!net->configured || slot >= EC_PEER_CAPACITY ||
	    (mask & ~EC_ACK_MASK) || !ec_identity_valid(device) ||
	    !memcmp(device, net->gateway, 4))
		return 0;
	existing = find(net, device);
	if (existing >= 0 && existing != (int)slot)
		return -1;
	p = &net->slots[slot];
	if (!p->used)
		net->count++;
	memcpy(p->device, device, 4);
	p->ack_mask = mask;
	p->used = 1;
	return 1;
}

int ec_peer_del(ec_network_t *net, unsigned slot)
{
	if (!net->configured || slot >= EC_PEER_CAPACITY)
		return 0;
	if (net->slots[slot].used)
		net->count--;
	memset(&net->slots[slot], 0, sizeof(net->slots[slot]));
	return 1;
}

/**
 * @brief Describe the identities needed for one device's wire policy.
 *
 * @param net Network supplying the gateway identity and tag.
 * @param device Four-byte device identity.
 *
 * @return Gateway, device and tag values for protocol validation.
 */
static ec_peer_t peer(const ec_network_t *net, const uint8_t *device)
{
	ec_peer_t p;

	memcpy(p.gateway, net->gateway, 4);
	memcpy(p.device, device, 4);
	p.tag = net->tag;
	return p;
}

size_t ec_network_ack(const ec_network_t *net, const uint8_t *raw, size_t len,
		      uint8_t *out, size_t capacity)
{
	ec_peer_t p;
	unsigned bit;
	int slot;

	if (!net->configured || len != 39 || raw[19] != net->tag ||
	    raw[21] != 0x9e)
		return 0;
	switch (raw[22]) {
	case 5:
		bit = 1;
		break;
	case 0x15:
		bit = 2;
		break;
	case 0xa0:
		bit = 4;
		break;
	case 8:
		bit = 8;
		break;
	default:
		return 0;
	}
	slot = find(net, raw + 7);
	if (slot < 0 || !(net->slots[slot].ack_mask & bit))
		return 0;
	if (memcmp(raw + 3, net->gateway, 4) &&
	    !(net->slots[slot].ack_mask & 0x10))
		return 0;
	p = peer(net, net->slots[slot].device);
	return ec_make_ack(&p, raw, len, out, capacity);
}

int ec_network_tx_allowed(const ec_network_t *net, const uint8_t *raw,
			  size_t len)
{
	static const uint8_t broadcast[4] = { 255, 255, 255, 255 };
	ec_peer_t p;

	if (!net->configured || len < 11 || len > 125)
		return 0;
	if (memcmp(raw + 3, broadcast, 4) && memcmp(raw + 3, net->gateway, 4) &&
	    find(net, raw + 3) < 0)
		return 0;
	/* Existing wire validation remains independent of table membership. */
	p = peer(net, raw + 3);
	return ec_tx_allowed(&p, raw, len);
}
