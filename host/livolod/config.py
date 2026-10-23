"""Strict, deliberately small configuration surface; identities are wire order."""

from dataclasses import dataclass, field
import json
import ipaddress
from pathlib import Path
import re


def integer(value, low, high, name):
    """Validate an integer setting against its supported inclusive range.

    @param value Candidate setting.
    @param low Smallest permitted integer.
    @param high Largest permitted integer.
    @param name Setting label used in validation errors.
    @return The validated integer.
    """
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def hex_value(value, size, name):
    """Validate and normalize an identifier expressed in wire-order hexadecimal.

    @param value Candidate hexadecimal string.
    @param size Required number of hexadecimal characters.
    @param name Field label used in validation errors.
    @return The lowercase hexadecimal string.
    """
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{%d}" % size, value):
        raise ValueError(f"{name} must be {size} hexadecimal characters in wire order")
    return value.lower()


def identity(value):
    """Require a usable unicast gateway or device identity.

    @param value Eight-character wire-order hexadecimal identity.
    @return The normalized identity.
    """
    value = hex_value(value, 8, "identity")
    if value in ("00000000", "ffffffff"):
        raise ValueError("Identity must be nonzero and nonbroadcast")
    return value


def fields(value, allowed, name):
    """Reject non-object settings and unknown fields before applying configuration.

    @param value Candidate settings object.
    @param allowed Space-separated list of accepted field names.
    @param name Object label used in validation errors.
    """
    if not isinstance(value, dict) or set(value) - set(allowed.split()):
        raise ValueError(f"Invalid or unknown fields in {name}")


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    slot: int
    outputs: int
    suffix: str = "0002"

    @property
    def mask(self):
        """Expose the output bits supported by this device.

        @return A bit mask covering the configured output channels.
        """
        return (1 << self.outputs) - 1


@dataclass(frozen=True)
class Config:
    gateway: str
    tag: str
    devices: tuple
    serial_port: str
    database: str
    mqtt: dict
    offline_after: int = 180
    heartbeat_parameter: int = 1
    web: dict = field(
        default_factory=lambda: {"enabled": False, "host": "127.0.0.1", "port": 8080}
    )
    output_mode: str = "disabled"


def load(path):
    """Load and validate a daemon configuration from disk.

    @param path Path to the JSON configuration file.
    @return The validated Config instance.
    """
    return parse(json.loads(Path(path).read_text()))


def parse(raw):
    """Validate daemon settings and resolve defaults without opening resources.

    @param raw Decoded JSON configuration object.
    @return The validated Config instance.
    """
    fields(
        raw,
        "network serial_port database mqtt devices offline_after web output_mode",
        "configuration",
    )
    output_mode = raw.get("output_mode", "disabled")
    if output_mode not in ("disabled", "experimental_legacy", "experimental_phase"):
        raise ValueError(
            "output_mode must be disabled, experimental_legacy or experimental_phase"
        )
    network = raw["network"]
    fields(network, "gateway tag heartbeat_parameter", "network")
    gateway = identity(network["gateway"])
    tag = hex_value(network["tag"], 2, "tag")
    if tag in ("00", "ff"):
        raise ValueError("Network tag must be 01..fe")
    devices = []
    if not isinstance(raw.get("devices"), list) or len(raw["devices"]) > 256:
        raise ValueError("devices must be a list of at most 256 configured peers")
    for entry in raw["devices"]:
        fields(entry, "id name slot outputs suffix", "device")
        device = Device(
            identity(entry["id"]),
            entry["name"],
            integer(entry["slot"], 0, 255, "slot"),
            integer(entry["outputs"], 1, 2, "outputs"),
            hex_value(entry.get("suffix", "0002"), 4, "suffix"),
        )
        if not isinstance(device.name, str) or not 1 <= len(device.name) <= 100:
            raise ValueError("Device name must contain 1..100 characters")
        if device.id == gateway or any(
            d.id == device.id or d.slot == device.slot for d in devices
        ):
            raise ValueError(
                "Device IDs and slots must be unique and distinct from gateway"
            )
        devices.append(device)
    mqtt = mqtt_config(raw["mqtt"], gateway)
    for key in ("serial_port", "database"):
        if (
            not isinstance(raw.get(key), str)
            or not raw[key].startswith("/")
            or "\0" in raw[key]
        ):
            raise ValueError(f"{key} must be an absolute path")
    web = dict(raw.get("web", {}))
    fields(web, "enabled host port token_env", "web")
    web.setdefault("enabled", False)
    web.setdefault("host", "127.0.0.1")
    web.setdefault("port", 8080)
    if (
        type(web["enabled"]) is not bool
        or not isinstance(web["host"], str)
        or not web["host"]
    ):
        raise ValueError("web.enabled must be boolean and web.host must be nonempty")
    if "token_env" in web and (
        not isinstance(web["token_env"], str) or not web["token_env"]
    ):
        raise ValueError("web.token_env must name an environment variable")
    if web["host"] not in ("127.0.0.1", "::1", "localhost") and not web.get(
        "token_env"
    ):
        raise ValueError("A non-loopback web host requires token_env")
    integer(web["port"], 1, 65535, "web.port")
    return Config(
        gateway,
        tag,
        tuple(devices),
        raw["serial_port"],
        raw["database"],
        mqtt,
        integer(raw.get("offline_after", 180), 10, 86400, "offline_after"),
        integer(
            network.get("heartbeat_parameter", 1), 0, 0xFFFFFFFF, "heartbeat_parameter"
        ),
        web,
        output_mode,
    )


def mqtt_config(raw, gateway, *, allow_password=False):
    """Validate broker settings and fill gateway-specific connection defaults.

    @param raw Candidate MQTT settings mapping.
    @param gateway Gateway identity used in the default MQTT client ID.
    @param allow_password Whether a saved plaintext password field is accepted.
    @return A validated copy of the MQTT settings.
    """
    mqtt = dict(raw)
    fields(
        mqtt,
        "host port base_topic client_id username password_env tls_ca tls_cert tls_key"
        + (" password" if allow_password else ""),
        "mqtt",
    )
    if not isinstance(mqtt.get("host"), str) or not mqtt["host"]:
        raise ValueError("mqtt.host is required")
    mqtt["port"] = integer(mqtt.get("port", 1883), 1, 65535, "MQTT port")
    mqtt.setdefault("base_topic", "livolo")
    mqtt.setdefault("client_id", "livolod-" + gateway)
    for key, value in mqtt.items():
        if key != "port" and (not isinstance(value, str) or not value or "\0" in value):
            raise ValueError(f"mqtt.{key} must be a nonempty string")
    if (
        any(c in mqtt["base_topic"] for c in "+#")
        or mqtt["base_topic"].startswith("/")
        or mqtt["base_topic"].endswith("/")
    ):
        raise ValueError("base_topic must be a concrete MQTT topic prefix")
    if bool(mqtt.get("tls_cert")) != bool(mqtt.get("tls_key")):
        raise ValueError("tls_cert and tls_key must be supplied together")
    if mqtt.get("tls_cert") and not mqtt.get("tls_ca"):
        raise ValueError("Client certificates require tls_ca")
    if mqtt.get("password_env") and not mqtt.get("username"):
        raise ValueError("password_env requires username")
    if len(mqtt["host"]) > 253 or any(
        c.isspace() or c in "/\\@?#" for c in mqtt["host"]
    ):
        raise ValueError(
            "MQTT server must be a hostname or IP address, without a scheme or port"
        )
    if ":" in mqtt["host"]:
        try:
            ipaddress.IPv6Address(mqtt["host"])
        except ValueError:
            raise ValueError("Enter the MQTT port in the separate Port field") from None
    if any(
        len(value.encode("utf-8")) > 1024
        for value in mqtt.values()
        if isinstance(value, str)
    ):
        raise ValueError("MQTT values must be at most 1024 UTF-8 bytes")
    if "password" in mqtt and (not mqtt.get("username") or mqtt.get("password_env")):
        raise ValueError(
            "A saved password requires a username and replaces password_env"
        )
    return mqtt
