"""Validate independent software identities without executing release source code."""

import ast
import json
from pathlib import Path
import re

NUMBER = r"(?:0|[1-9][0-9]*)"


def numeric_version(value, components):
    """Reject ambiguous numeric identifiers and return components for comparison.

    @param value Version string without prefixes, leading zeros or suffixes.
    @param components Required number of numeric components, two for firmware.
    @return Integer tuple; raises ValueError for malformed or unbounded firmware.
    """
    if not isinstance(value, str) or not re.fullmatch(
        NUMBER + (r"\." + NUMBER) * (components - 1), value
    ):
        raise ValueError("Invalid numeric version: " + repr(value))
    parts = tuple(map(int, value.split(".")))
    if components == 2 and max(parts) > 65535:
        raise ValueError("Firmware version component exceeds 65535")
    return parts


def app_config_version(config, version):
    """Give an isolated app build its source version without promoting the public listing.

    @param config Supervisor descriptor text containing one numeric version scalar.
    @param version Validated distribution version of the source being built.
    @return Descriptor text with only its version scalar replaced.
    """
    numeric_version(version, 3)
    pattern = r"^version:[ \t]*(?P<quote>[\"']?)[0-9]+\.[0-9]+\.[0-9]+(?P=quote)[ \t]*$"
    if len(re.findall(r"^version:", config, re.M)) != 1:
        raise ValueError("App descriptor must have exactly one version")
    result, count = re.subn(pattern, f'version: "{version}"', config, flags=re.M)
    if count != 1:
        raise ValueError("App descriptor version must be a numeric scalar")
    return result


def normalize_metadata(metadata):
    """Resolve legacy equal versions or require explicit schema-2 compatibility.

    @param metadata Release record with daemon_version and optional schema fields.
    @return New record with explicit schema and firmware_compat_version; input is unchanged.
    """
    result = dict(metadata)
    schema = result.get("schema", 1)
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError("Unsupported release version schema")
    daemon = result["daemon_version"]
    if schema == 1:
        numeric_version(daemon, 2)
        if result.get("firmware_compat_version", daemon) != daemon:
            raise ValueError("Legacy daemon and firmware versions must match")
        result["firmware_compat_version"] = daemon
    else:
        numeric_version(daemon, 3)
        numeric_version(result.get("firmware_compat_version"), 2)
    result["schema"] = schema
    return result


def source_versions(source):
    """Check package/runtime/compatibility identities in an exported source tree.

    @param source Root of exact source export, including VERSION and host package files.
    @return Normalized release metadata; raises ValueError on missing or inconsistent identities.
    """
    source = Path(source)
    compatibility = (source / "VERSION").read_text().strip()
    numeric_version(compatibility, 2)
    matches = re.findall(
        r'(?m)^version = "([^"]+)"$',
        (source / "host/pyproject.toml").read_text(),
    )
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous daemon package version")
    metadata_path = source / "host/release/version.json"
    metadata = normalize_metadata(
        json.loads(metadata_path.read_text())
        if metadata_path.is_file()
        else {"daemon_version": matches[0]}
    )
    constants = {}
    tree = ast.parse((source / "host/livolod/__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "__version__",
                    "FIRMWARE_COMPAT_VERSION",
                ):
                    if target.id in constants:
                        raise ValueError("Duplicate runtime version constant")
                    constants[target.id] = ast.literal_eval(node.value)
    if (
        metadata["daemon_version"] != matches[0]
        or constants.get("__version__") != matches[0]
    ):
        raise ValueError("Daemon package/runtime version mismatch")
    runtime_compat = constants.get("FIRMWARE_COMPAT_VERSION")
    if metadata["schema"] == 1 and runtime_compat is None:
        runtime_compat = constants.get("__version__")
    if (
        metadata["firmware_compat_version"] != compatibility
        or runtime_compat != compatibility
    ):
        raise ValueError("Firmware compatibility version mismatch")
    catalog_path = source / "host/livolod/firmware/manifest.json"
    if catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text())
        if (
            set(catalog) != {"schema", "host_version", "artifacts"}
            or type(catalog["schema"]) is not int
            or catalog["schema"] != 1
            or catalog["host_version"] != compatibility
            or not isinstance(catalog["artifacts"], list)
        ):
            raise ValueError("Firmware catalog compatibility mismatch")
    elif metadata["schema"] == 2:
        raise ValueError("Missing firmware catalog")
    header = source / "firmware/version.h"
    if header.is_file():
        for name, expected in zip(("MAJOR", "MINOR"), compatibility.split(".")):
            values = re.findall(
                r"(?m)^#define EC_VERSION_" + name + r" ([0-9]+)$", header.read_text()
            )
            if values != [expected]:
                raise ValueError("Firmware header compatibility mismatch")
    return metadata
