"""Bounded local catalog inspection and atomic operator-only provisioning."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from .firmware_bundle import BundleError, _object, _read, _manifest, select_candidate

MAX_CATALOG_BYTES = 16 * 1024 * 1024


def inventory(directory):
    """Read bounded references and filesystem identities without following file links.

    @param directory Explicit operator catalog root; never supplied by HTTP.
    @return Root, manifest bytes, reference names and a metadata change stamp.
    """
    try:
        root = Path(directory).resolve()
        if not root.is_dir() or not (root / "manifest.json").exists():
            raise BundleError("bundle_missing", "No firmware catalog is installed")
        raw, _ = _read(root, "manifest.json", 65536)
        manifest = json.loads(raw, object_pairs_hook=_object)
        entries = manifest["artifacts"]
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError("Too many catalog entries")
        names = {"manifest.json"}
        for entry in entries:
            notices = entry["notices"]
            if not isinstance(notices, list) or len(notices) > 32:
                raise ValueError("Too many notices")
            names.update([entry["path"], entry["qualification"], *notices])
            if "public_key" in entry:
                names.add(entry["public_key"])
        stamps, total = [], 0
        for name in sorted(names):
            if not isinstance(name, str) or "\\" in name:
                raise ValueError("Invalid catalog path")
            parts = PurePosixPath(name)
            if parts.is_absolute() or any(
                p in ("", ".", "..") for p in name.split("/")
            ):
                raise ValueError("Catalog path escapes root")
            path = root
            for part in parts.parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError("Catalog reference is a symlink")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Catalog reference is not a regular file")
            total += info.st_size
            if info.st_size > 1048576 or total > MAX_CATALOG_BYTES:
                raise ValueError("Catalog exceeds validation budget")
            stamps.append(
                (
                    name,
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
        return (
            root,
            raw,
            names,
            (str(root), hashlib.sha256(raw).hexdigest(), tuple(stamps)),
        )
    except BundleError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        raise BundleError(
            "bundle_invalid", "Invalid or unavailable catalog files"
        ) from error


def validate(directory, host_version):
    """Validate every artifact and enforce aggregate bounds without accessing hardware.

    @param directory Local source or staged catalog.
    @param host_version Exact radio compatibility target, not the daemon package version.
    @return Validated manifest mapping.
    """
    root, raw, _, before = inventory(directory)
    _, entries = _manifest(root, host_version)
    for entry in entries:
        select_candidate(
            root,
            board=entry["board"],
            host_version=host_version,
            app_baudrate=entry["app_baudrate"],
        )
    if inventory(directory)[3] != before:
        raise BundleError("bundle_invalid", "Catalog changed during validation")
    return json.loads(raw)


@contextmanager
def publication_lock(root):
    """Serialize local publishers; no daemon database or radio locks are acquired.

    @param root Existing nonsymlink destination directory.
    @yield Exclusive publication ownership.
    """
    descriptor = os.open(
        root / ".catalog.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Catalog lock must be a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write(path, data):
    """Durably replace one file using a temporary file in the same directory.

    @param path Destination file in an operator-controlled directory.
    @param data Immutable bytes to publish.
    """
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def provision(source, destination, host_version, manifest_sha256=None):
    """Publish a validated catalog with immutable files and a manifest commit point.

    @param source Local directory containing a reviewed catalog, not an archive.
    @param destination Persistent operator-owned absolute catalog directory.
    @param host_version Exact radio compatibility target, not the daemon package version.
    @param manifest_sha256 Optional expected source manifest digest.
    @return Public publication summary, without file contents or credentials.
    """
    source_root, raw, names, _ = inventory(source)
    if manifest_sha256 and hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError("Source manifest digest does not match")
    target = Path(destination)
    if not target.is_absolute() or target.is_symlink():
        raise ValueError("Destination must be an absolute nonsymlink directory")
    target = target.parent.resolve() / target.name
    if source_root == target.resolve():
        raise ValueError("Stage the source separately from the active catalog")
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    with (
        publication_lock(target),
        tempfile.TemporaryDirectory(prefix=".stage-", dir=target) as temp,
    ):
        stage = Path(temp)
        # Snapshot only bounded referenced bytes. Validation uses this private copy.
        total = 0
        for name in names:
            data = raw if name == "manifest.json" else _read(source_root, name)[0]
            total += len(data)
            if total > MAX_CATALOG_BYTES:
                raise ValueError("Catalog exceeds validation budget")
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = validate(stage, host_version)
        objects = target / "objects"
        history = target / "history"
        for directory in (objects, history):
            if directory.is_symlink():
                raise ValueError("Catalog directories must not be links")
            directory.mkdir(mode=0o755, exist_ok=True)
        mapped = {}
        for name in names - {"manifest.json"}:
            data = (stage / name).read_bytes()
            relative = "objects/" + hashlib.sha256(data).hexdigest()
            path = target / relative
            if path.exists() or path.is_symlink():
                if path.is_symlink() or _read(target, relative)[0] != data:
                    raise ValueError("Existing immutable catalog object is damaged")
            else:
                _write(path, data)
            mapped[name] = relative
        for entry in manifest["artifacts"]:
            for key in ("path", "qualification", "public_key"):
                if key in entry:
                    entry[key] = mapped[entry[key]]
            entry["notices"] = [mapped[name] for name in entry["notices"]]
        published = (json.dumps(manifest, indent=2) + "\n").encode()
        current = target / "manifest.json"
        if current.exists() or current.is_symlink():
            previous, _ = _read(target, "manifest.json", 65536)
            _write(history / (hashlib.sha256(previous).hexdigest() + ".json"), previous)
        for directory in (objects, history):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _write(current, published)
        fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return dict(
        host_version=host_version,
        artifacts=len(manifest["artifacts"]),
        source_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        manifest_sha256=hashlib.sha256(published).hexdigest(),
    )
