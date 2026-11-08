"""Fixed reviewed Gecko board contracts; no HTTP-selectable transport or security overrides."""

from dataclasses import dataclass
from types import MappingProxyType

from .firmware_bundle import BundleError


@dataclass(frozen=True)
class GeckoProfile:
    """Immutable board/transport/menu facts; exact-unit security policy remains separate."""

    board: str
    label: str
    vid: int
    pid: int
    serial_required: bool
    board_evidence: str
    loader_version: str
    loader_wire_versions: tuple
    menu_request: bytes
    modem_asserted: bool


PROFILES = MappingProxyType(
    {
        "zb-gw04-1v2": GeckoProfile(
            "zb-gw04-1v2",
            "ZB-GW04 1v2",
            0x1A86,
            0x7523,
            False,
            "operator_confirmed_zbgw04_1v2",
            "1.9.2",
            (b"1.9.2",),
            b"\n3",
            False,
        ),
        "sonoff-zbdongle-e": GeckoProfile(
            "sonoff-zbdongle-e",
            "SONOFF ZBDongle-E",
            0x10C4,
            0xEA60,
            True,
            "operator_confirmed_sonoff_zbdongle_e",
            "2.4.2",
            (b"2.04.02", b"2.4.2"),
            b"\n",
            True,
        ),
    }
)


def gecko_profile(board="zb-gw04-1v2"):
    """Resolve only a supported server-owned profile, retaining the existing ZB default.

    @param board Exact profile identifier selected by protected daemon configuration.
    @return Immutable reviewed facts, never values supplied by an API client.
    """
    if not isinstance(board, str) or board not in PROFILES:
        raise BundleError("unsupported_profile", "Unsupported preserved-Gecko profile")
    return PROFILES[board]


def evidence_profile(evidence):
    """Resolve an explicit operator board assertion without inferring it from USB IDs.

    @param evidence Exact server-owned board confirmation constant.
    """
    for profile in PROFILES.values():
        if evidence == profile.board_evidence:
            return profile
    raise BundleError("board_evidence_required", "Confirm the actual supported board")
