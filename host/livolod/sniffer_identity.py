"""Strict passive identity proof for the one reviewed Sonoff sniffer entry method."""

import binascii
import struct

from .firmware_bundle import BundleError

ENTRY_METHOD = "sonoff_sniffer_0_12"
SNIFFER_BAUD = 921600
HEADER = struct.Struct("<4sHBBIQ")
MARKER = b"EC-MG21-BOARD=sonoff-zbdongle-e\0"
MAX_BYTES = 300000


class SnifferIdentity:
    """Require fresh progressing CRC-valid status records without transmitting probes."""

    def __init__(self):
        """Start an empty bounded observation; cached identities cannot satisfy entry."""
        self.buffer = bytearray()
        self.total = self.discarded = self.statuses = 0
        self.previous = self.timestamp = None

    def feed(self, data):
        """Validate fragmented ESN1 traffic and return whether two statuses were proved.

        @param data Newly received bytes from the currently held serial descriptor.
        """
        self.total += len(data)
        if self.total > MAX_BYTES:
            raise BundleError(
                "application_unrecognized", "Sniffer byte budget exceeded"
            )
        self.buffer.extend(data)
        while len(self.buffer) >= HEADER.size:
            if self.buffer[:4] != b"ESN1":
                position = self.buffer.find(b"ESN1", 1)
                count = position if position >= 0 else len(self.buffer) - 3
                self.discarded += count
                del self.buffer[:count]
                if self.previous is not None or self.discarded > 512:
                    raise BundleError(
                        "application_unrecognized", "Invalid sniffer framing"
                    )
                continue
            _, length, kind, flags, sequence, timestamp = HEADER.unpack_from(
                self.buffer
            )
            if length > 129 or kind not in (1, 2) or flags != 1:
                raise BundleError("application_unrecognized", "Invalid sniffer header")
            size = HEADER.size + length + 4
            if len(self.buffer) < size:
                break
            wire = bytes(self.buffer[:size])
            del self.buffer[:size]
            if binascii.crc32(wire[:-4]) != struct.unpack_from("<I", wire, size - 4)[0]:
                raise BundleError("application_unrecognized", "Invalid sniffer CRC")
            if self.previous is not None:
                advance = (sequence - self.previous) & 0xFFFFFFFF
                if not 0 < advance < 0x80000000:
                    raise BundleError(
                        "application_unrecognized", "Sniffer sequence changed"
                    )
            self.previous = sequence
            body = wire[HEADER.size : -4]
            if kind == 1:
                if not 5 <= length <= 129 or body[2:4] != b"\x0b\x00":
                    raise BundleError(
                        "application_unrecognized", "Invalid sniffer RX record"
                    )
                continue
            if (
                length != 36 + len(MARKER)
                or body[36:] != MARKER
                or struct.unpack_from("<HHI", body) != (0, 12, SNIFFER_BAUD)
                or (self.timestamp is not None and timestamp <= self.timestamp)
            ):
                raise BundleError(
                    "application_unrecognized", "Unsupported sniffer status"
                )
            self.timestamp = timestamp
            self.statuses += 1
        return self.statuses >= 2
