"""Shared offline application validation for catalog updates and local flashing."""

import hashlib
from pathlib import Path

from .firmware_bundle import BOARDS, BundleError, _gbl, _image_identity, _read
from .version import parse_version


def inspect_artifact_bytes(data, *, board, version, public_key=None):
    """Check exact-board image structure before any serial or flashing operation.

    @param data Immutable complete application container bytes, at most one MiB.
    @param board Exact supported board identifier; never inferred from a USB bridge.
    @param version Expected major.minor compiled release.
    @param public_key Raw 32-byte TI verification key; required for signed TI packages.
    @return Public compatibility metadata, excluding payloads and key material.
    """
    if not isinstance(board, str) or board not in BOARDS:
        raise BundleError("unsupported_board", "Unsupported firmware board")
    try:
        parse_version(version)
        if not isinstance(data, bytes) or not 256 <= len(data) <= 1024 * 1024:
            raise ValueError("Invalid application package size")
        if board == "codm-cc2652p":
            if not isinstance(public_key, bytes) or len(public_key) != 32:
                raise BundleError(
                    "signing_key_mismatch", "TI requires a raw 32-byte verification key"
                )
            try:
                from cryptography.exceptions import InvalidSignature
                from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                    Ed25519PublicKey,
                )
            except ImportError as error:
                raise BundleError(
                    "backend_unavailable", "TI signature verification is not installed"
                ) from error
            from .update_package import inspect_package

            try:
                info = inspect_package(
                    data, Ed25519PublicKey.from_public_bytes(public_key)
                )
            except InvalidSignature as error:
                raise ValueError("Invalid TI application signature") from error
            if info["version"] != version:
                raise ValueError(
                    "Signed TI version does not match the expected release"
                )
            image = data[192:]
            _image_identity(image, board, version)
            specific = dict(
                image_sha512=info["sha512"],
                flash_start=0x8000,
                flash_end=0x8000 + len(image),
                authentication="ed25519",
            )
        elif board == "nordic-pca10059":
            from .nrf_dfu import inspect_package

            return inspect_package(data, version=version)
        else:
            image = _gbl(data, board, version)
            specific = dict(
                flash_start=0x4000,
                flash_end=0x4000 + len(image),
                authentication="trusted_local_package",
            )
        return dict(
            board=board,
            version=version,
            protocol=3,
            sha256=hashlib.sha256(data).hexdigest(),
            application_bytes=len(image),
            application_sha256=hashlib.sha256(image).hexdigest(),
            bootloader_preserved=True,
            rollback_supported=board == "codm-cc2652p",
            **specific,
        )
    except BundleError:
        raise
    except ValueError as error:
        raise BundleError("artifact_invalid", str(error)) from error


def inspect_artifact(path, *, board, version, public_key=None, operation="update"):
    """Read a bounded local application and return a private validated candidate.

    @param path Absolute operator-selected local application container path.
    @param board Exact board identifier supplied by the local operator.
    @param version Expected compiled major.minor release.
    @param public_key Absolute path to the raw TI public key, or None for other boards.
    @param operation Update or explicit recovery; neither implicitly authorizes flashing.
    @return Candidate with immutable _data; never serialize this whole dictionary.
    """
    if operation not in ("update", "recovery"):
        raise BundleError(
            "unsupported_operation",
            "Application inspection supports update or recovery",
        )
    location = Path(path)
    if not location.is_absolute():
        raise BundleError("artifact_invalid", "Artifact path must be absolute")
    data, absolute = _read(location.parent.resolve(), location.name)
    key = None
    if public_key is not None:
        location = Path(public_key)
        if not location.is_absolute():
            raise BundleError(
                "signing_key_mismatch", "Public-key path must be absolute"
            )
        key, _ = _read(location.parent.resolve(), location.name, 32)
    inspection = inspect_artifact_bytes(
        data, board=board, version=version, public_key=key
    )
    ident = "local-" + board
    return dict(
        id=ident + ":" + inspection["sha256"],
        artifact_id=ident,
        board=board,
        version=version,
        backend=BOARDS[board][0],
        app_baudrate=460800,
        sha256=inspection["sha256"],
        path=absolute,
        public_key=key.hex() if key is not None else None,
        image_sha512=inspection.get("image_sha512"),
        rollback_supported=inspection["rollback_supported"],
        bootloader_min="0.2" if board == "codm-cc2652p" else None,
        inspection=inspection,
        operation=operation,
        _data=data,
    )
