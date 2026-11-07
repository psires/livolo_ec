"""Structural inspection of the exact supported TI bootstrap layout.

This is not signature verification, provenance verification or hardware
qualification. A caller admitting a public bootloader must separately bind its
complete SHA-256 and public-key fingerprint to reviewed source/build inputs and
an independent physical first-install/recovery record. An arbitrary executable
can contain plausible vectors, a version marker and somebody else's public key.
Never use this inspector alone as permission to flash or publish an image.
"""

import hashlib
import struct


def inspect_ti_bootloader(data, *, public_key, version="0.2"):
    """Check the raw CC2652P bootloader without accepting factory or application data.

    @param data Immutable raw bootloader bytes linked at address zero.
    @param public_key Exact raw 32-byte Ed25519 key selected by the release review.
    @param version Explicit supported resident-bootloader version, currently only 0.2.
    @return Structural metadata; raises ValueError for an unsupported or invalid image.
    """
    if version != "0.2":
        raise ValueError("Only the reviewed TI bootloader 0.2 layout is supported")
    if not isinstance(data, bytes) or not 256 <= len(data) <= 0x8000 or len(data) % 4:
        raise ValueError("Raw TI bootloader must be 256..32768 bytes, word aligned")
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ValueError("TI bootstrap requires an exact 32-byte raw public key")
    marker = b"EC-BOOT 0.2\0"
    metadata_end = 8 + len(marker)
    if data[8:metadata_end] != marker or data.count(b"EC-BOOT ") != 1:
        raise ValueError("TI bootloader version marker is missing or ambiguous")
    stack, reset = struct.unpack_from("<II", data)
    entry = reset & ~1
    if (
        stack != 0x20014000
        or not reset & 1
        or not metadata_end <= entry <= len(data) - 2
    ):
        raise ValueError("TI bootloader vectors do not match its reviewed layout")
    key_offset = data.find(public_key)
    if key_offset < metadata_end or data.find(public_key, key_offset + 1) != -1:
        raise ValueError(
            "TI bootloader must contain the selected public key exactly once"
        )
    return dict(
        board="codm-cc2652p",
        role="bootloader",
        version=version,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        public_key_sha256=hashlib.sha256(public_key).hexdigest(),
        public_key_offset=key_offset,
        flash_start=0,
        flash_end=len(data),
        application_base=0x8000,
        initial_sp=stack,
        reset_vector=reset,
        validation="structure_only",
    )
