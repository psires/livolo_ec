"""Only the frame layouts established in analysis/protocol.md are supported."""

import binascii
from dataclasses import dataclass


def frame(gateway, tag, device, suffix, sequence, opcode, data):
    """Create a direct EC application command for a configured device.

    @param gateway Source gateway identity in wire-order hexadecimal.
    @param tag Two-character network tag.
    @param device Destination device identity in wire-order hexadecimal.
    @param suffix Four-character device suffix.
    @param sequence Eight-bit radio sequence number.
    @param opcode Application command opcode.
    @param data Eight-byte application payload.
    @return The complete 41-byte command frame.
    """
    if len(data) != 8:
        raise ValueError("EC application data must be eight bytes")
    raw = bytearray(41)
    raw[:3] = bytes((1, 0x88, sequence))
    raw[3:7] = bytes.fromhex(device)
    raw[7:11] = bytes.fromhex(gateway)
    raw[15:19] = bytes.fromhex(device)
    raw[19:23] = bytes((int(tag, 16), 0x1E, 0x8C, opcode))
    raw[23:29] = bytes.fromhex(device + suffix)
    raw[29:37] = data
    raw[37] = sum(raw[21:37]) & 255
    return bytes(raw)


def heartbeat(gateway, tag, sequence, seconds, parameter):
    """Create the network heartbeat that establishes the gateway time reference.

    @param gateway Source gateway identity in wire-order hexadecimal.
    @param tag Two-character network tag.
    @param sequence Eight-bit radio sequence number.
    @param seconds Gateway heartbeat epoch in seconds.
    @param parameter Unsigned 32-bit heartbeat parameter.
    @return The broadcast heartbeat frame.
    """
    data = (seconds & 0xFFFFFFFF).to_bytes(4, "little") + parameter.to_bytes(
        4, "little"
    )
    raw = bytearray(frame(gateway, tag, "ffffffff", "0000", sequence, 8, data))
    raw[15:19] = bytes(4)
    raw[20] = 0x0E
    raw[23:29] = bytes(6)
    raw[37] = sum(raw[21:37]) & 255
    return bytes(raw)


@dataclass(frozen=True)
class Packet:
    source: str
    destination: str
    subject: str
    suffix: str
    tag: str
    family: int
    opcode: int
    data: bytes


def decode(raw):
    """Validate a supported EC frame and expose its application fields.

    @param raw Received frame bytes including the application checksum.
    @return A decoded Packet; invalid frames raise ValueError.
    """
    if len(raw) not in (39, 41) or raw[:2] != b"\x01\x88":
        raise ValueError("Unsupported EC frame")
    family = raw[21]
    if family == 0x8C and len(raw) == 41:
        valid = raw[37] == sum(raw[21:37]) & 255
    elif family == 0x9E and len(raw) == 39:
        valid = int.from_bytes(raw[37:39], "big") == binascii.crc_hqx(
            raw[21:37], 0xFFFF
        )
    else:
        raise ValueError("Unsupported EC family")
    if not valid:
        raise ValueError("Bad application checksum")
    return Packet(
        raw[7:11].hex(),
        raw[3:7].hex(),
        raw[23:27].hex(),
        raw[27:29].hex(),
        raw[19:20].hex(),
        family,
        raw[22],
        raw[29:37],
    )


def rx_envelope(line):
    """Validate a UART receive record independently of its application payload.

    @param line Complete RX record from the adapter.
    @return The receive sequence, RSSI, LQI, and raw frame bytes.
    """
    parts = line.split()
    if len(parts) != 7 or parts[0] != "RX":
        raise ValueError("Malformed RX record")
    sequence, ticks, rssi, lqi, length = map(int, parts[1:6])
    raw = bytes.fromhex(parts[6])
    if (
        length != len(raw)
        or not 0 <= sequence <= 0xFFFFFFFF
        or not 0 <= ticks <= 0xFFFFFFFF
    ):
        raise ValueError("Invalid RX length/counter")
    return sequence, rssi, lqi, raw


def rx_record(line):
    """Decode a UART receive record into radio metadata and a validated packet.

    @param line Complete RX record from the adapter.
    @return The receive sequence, RSSI, LQI, and Packet.
    """
    sequence, rssi, lqi, raw = rx_envelope(line)
    return sequence, rssi, lqi, decode(raw)


def key_values(text):
    """Extract named metadata from an adapter response or status record.

    @param text Whitespace-separated UART text containing key=value fields.
    @return A mapping of metadata names to string values.
    """
    return dict(part.split("=", 1) for part in text.split() if "=" in part)
