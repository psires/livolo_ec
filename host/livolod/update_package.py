#!/usr/bin/env python3
"""Create/verify signed application packages and one-time CC2652P installers.

Signing requires cryptography (see firmware/update/requirements-build.txt).
Private keys belong outside source control; this tool never prints their contents.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct

FLASH_SIZE = 0x58000
APP = 0x8000
SLOT = 0x18000
CURRENT_META = 0x52000
CCFG = 0x57FA8
HEADER = struct.Struct("<8s8I64s24s")
MANIFEST = 192
BOARD = 0x26520001
FORMAT = 0x10000


def version(value):
    """Validate a firmware release identifier for the signed manifest.

    @param value Release text in major.minor form without leading zeroes.
    @return The unsigned 16-bit major and minor components.
    """
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Version must be major.minor without leading zeroes")
    pair = tuple(map(int, value.split(".")))
    if max(pair) > 65535:
        raise ValueError("Version components must be at most 65535")
    return pair


def public_key(path):
    """Load the raw Ed25519 verification key used for firmware packages.

    @param path Path to a 32-byte public key file.
    @return An Ed25519PublicKey instance.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    raw = Path(path).read_bytes()
    if len(raw) != 32:
        raise ValueError("Public key must contain exactly 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def validate_image(image):
    """Reject applications that cannot execute within the firmware update slot.

    @param image Linked application image bytes including the vector table.
    """
    if not 256 <= len(image) <= SLOT or len(image) % 4:
        raise ValueError("Application must be 256..98304 bytes, word aligned")
    sp, pc = struct.unpack_from("<II", image)
    if (
        not 0x20000000 < sp <= 0x20014000
        or sp % 8
        or not pc & 1
        or not APP + 8 <= pc & ~1 < APP + len(image)
    ):
        raise ValueError("Application vectors do not match the update execution slot")


def inspect_package(data, key):
    """Authenticate a firmware package and validate its board and image contract.

    @param data Complete signed package bytes.
    @param key Ed25519 public key trusted for this board.
    @return Verified release, board, size, and digest metadata.
    """
    if len(data) < MANIFEST:
        raise ValueError("Truncated package")
    header, signature, image = data[:128], data[128:192], data[192:]
    magic, fmt, board, major, minor, proto, base, length, reserved, digest, padding = (
        HEADER.unpack(header)
    )
    if (
        magic != b"ECUPD01\0"
        or fmt != FORMAT
        or board != BOARD
        or proto != 3
        or base != APP
        or reserved
        or padding != bytes(24)
        or max(major, minor) > 65535
        or length != len(image)
    ):
        raise ValueError("Unsupported or malformed firmware manifest")
    validate_image(image)
    if hashlib.sha512(image).digest() != digest:
        raise ValueError("Image digest mismatch")
    key.verify(signature, header)
    return dict(
        version=f"{major}.{minor}",
        board="codm-cc2652p",
        protocol=proto,
        bytes=length,
        sha512=digest.hex(),
        package_sha256=hashlib.sha256(data).hexdigest(),
    )


def sign_image(image, release, private):
    """Create a signed update package for a version-matched application.

    @param image Linked application bytes containing the release marker.
    @param release Release identifier in major.minor form.
    @param private Path to an unencrypted Ed25519 PEM private key.
    @return Signed manifest followed by the application image.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    validate_image(image)
    major, minor = version(release)
    if b"EC-FW-VERSION=" + release.encode() + b"\0" not in image:
        raise ValueError("Compiled firmware version marker does not match the release")
    key = serialization.load_pem_private_key(Path(private).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Expected an Ed25519 private key")
    header = HEADER.pack(
        b"ECUPD01\0",
        FORMAT,
        BOARD,
        major,
        minor,
        3,
        APP,
        len(image),
        0,
        hashlib.sha512(image).digest(),
        bytes(24),
    )
    return header + key.sign(header) + image


def generate_keys(directory):
    """Create a new firmware signing identity without replacing existing key files.

    @param directory Destination directory for private, public, and C header key files.
    @return SHA-256 fingerprint of the public key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(
        (directory / n).exists()
        for n in ("signing.pem", "public.key", "update-public-key.h")
    ):
        raise ValueError(
            "Key files already exist; refusing to replace signing identity"
        )
    key = Ed25519PrivateKey.generate()
    secret = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    fd = os.open(directory / "signing.pem", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secret)
    (directory / "public.key").write_bytes(public)
    (directory / "update-public-key.h").write_text(
        "#define EC_UPDATE_PUBLIC_KEY {"
        + ",".join(f"0x{x:02x}" for x in public)
        + "}\n"
    )
    return hashlib.sha256(public).hexdigest()


def factory_image(boot, package, original, key_raw):
    """Build a first-install flash image while preserving the board configuration.

    @param boot Bootloader image bytes embedding the verification key.
    @param package Signed application package bytes.
    @param original Complete original flash backup supplying board configuration.
    @param key_raw Raw 32-byte Ed25519 public key.
    @return A validated full flash image.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from flash_cc26xx import validate_image as validate_factory

    inspect_package(package, Ed25519PublicKey.from_public_bytes(key_raw))
    if not 8 <= len(boot) <= APP or key_raw not in boot:
        raise ValueError("Bootloader size/key mismatch")
    if len(original) != FLASH_SIZE:
        raise ValueError("Factory backup must be a complete 352KiB image")
    data = bytearray(b"\xff" * FLASH_SIZE)
    data[: len(boot)] = boot
    data[APP : APP + len(package) - MANIFEST] = package[MANIFEST:]
    data[CURRENT_META : CURRENT_META + MANIFEST] = package[:MANIFEST]
    data[CCFG:] = original[CCFG:]
    validate_factory(data)
    return bytes(data)


def main():
    """Run firmware key generation, package signing, verification, or installer creation."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("directory")
    s = sub.add_parser("sign")
    s.add_argument("image")
    s.add_argument("--version", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--output", required=True)
    v = sub.add_parser("verify")
    v.add_argument("package")
    v.add_argument("--public-key", required=True)
    f = sub.add_parser("factory")
    f.add_argument("--bootloader", required=True)
    f.add_argument("--package", required=True)
    f.add_argument("--original", required=True)
    f.add_argument("--public-key", required=True)
    f.add_argument("--output", required=True)
    a = p.parse_args()
    if a.action == "keygen":
        print(json.dumps({"public_key_sha256": generate_keys(a.directory)}))
    elif a.action == "verify":
        print(
            json.dumps(
                inspect_package(Path(a.package).read_bytes(), public_key(a.public_key)),
                indent=2,
            )
        )
    else:
        data = (
            sign_image(Path(a.image).read_bytes(), a.version, a.key)
            if a.action == "sign"
            else factory_image(
                Path(a.bootloader).read_bytes(),
                Path(a.package).read_bytes(),
                Path(a.original).read_bytes(),
                Path(a.public_key).read_bytes(),
            )
        )
        with open(a.output, "xb") as out:
            out.write(data)
        print(
            json.dumps(
                {
                    "path": a.output,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        )


if __name__ == "__main__":
    main()
