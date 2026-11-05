"""Experimental profiles from the two physically verified EC pairing layouts.

This is a passive classifier, not a Zigbee endpoint interview or a universal
model decoder. Callers must pass checksum-validated Packet values and aggregate
current-session observations with freshness/conflict checks before auto-add.
"""

PROFILES = {
    "0001": (1, bytes.fromhex("000101053401")),
    "0002": (2, bytes.fromhex("000203053401")),
}


def pairing_profile(packet, *, gateway):
    """Classify a native unregistered pairing request against captured profiles.

    @param packet Packet returned by protocol.decode after checksum validation.
    @param gateway Configured gateway identity in lowercase wire-order hexadecimal.
    @return Evidence dictionary including unknown signatures, or None for unrelated traffic.
    """
    if (
        packet.family != 0x9E
        or packet.opcode != 0x01
        or packet.tag != "ff"
        or packet.source != packet.subject
        or packet.source in ("00000000", "ffffffff")
        or packet.destination not in (gateway, "ffffffff")
    ):
        return None

    # Byte six varies across retransmission groups in both captured layouts.
    # Its value does not strengthen profile evidence or create a new profile.
    signature = f"{packet.suffix}:{packet.data[:6].hex()}:{packet.data[7:].hex()}"
    result = {
        "experimental": True,
        "status": "unknown",
        "outputs": None,
        "suffix": packet.suffix,
        "profile": None,
        "reason": "unknown_pairing_suffix",
        "basis": "pairing_suffix_and_payload",
        "signature": signature,
    }
    known = PROFILES.get(packet.suffix)
    if known is None:
        return result
    outputs, prefix = known
    if len(packet.data) != 8 or packet.data[:6] != prefix or packet.data[7] != 0:
        result["reason"] = "unknown_pairing_payload"
        return result
    result.update(
        status="detected",
        outputs=outputs,
        profile=f"livolo-ec-suffix-{packet.suffix}",
        reason="known_pairing_signature",
    )
    return result
