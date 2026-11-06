"""Translate Supervisor options, grant serial access, then exec the non-root daemon."""

import json
import os
from pathlib import Path
import stat
import secrets

from livolod.config import parse


def configuration(options, data_path=Path("/data")):
    """Build native-only daemon settings; SQLite remains authoritative for devices.

    @param options Validated Supervisor options, still checked by daemon parsing.
    @param data_path Persistent app data directory.
    @return Configuration mapping without embedding API credentials.
    """
    token = options.get("api_token", "")
    if (
        not isinstance(token, str)
        or not token.isascii()
        or len(token) < 32
        or any(c.isspace() for c in token)
    ):
        raise ValueError("Set an API token of at least 32 non-whitespace characters")
    web_enabled = options.get("web_enabled", True)
    if type(web_enabled) is not bool:
        raise ValueError("web_enabled must be a boolean")
    catalog = options.get("firmware_catalog", "bundled")
    if catalog not in ("bundled", "local"):
        raise ValueError("firmware_catalog must be bundled or local")
    raw = {
        "network": {
            "gateway": options["gateway"].lower(),
            "tag": options["tag"].lower(),
        },
        "serial_port": options["serial_port"],
        "serial_baudrate": int(options["serial_baudrate"]),
        "database": str(data_path / "state.sqlite3"),
        "mqtt": {"enabled": False},
        "devices": [],
        "output_mode": options.get("output_mode", "experimental_phase"),
        "api": {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8090,
            "token_env": "LIVOLO_API_TOKEN",
        },
        "web": {"enabled": False},
    }
    if web_enabled:
        raw["web"] = {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8099,
            "ingress": {
                "enabled": True,
                "trusted_proxies": ["172.30.32.2"],
                "allowed_origins": [options["ha_origin"]],
            },
        }
    if catalog == "local":
        raw["firmware_bundle_dir"] = str(data_path / "firmware")
    return raw


def prepare_data(data_path, raw, uid=10001, gid=10001):
    """Prepare app-owned files without following links during privileged startup.

    @param data_path Existing private data directory.
    @param raw Validated non-secret daemon configuration.
    @param uid Runtime daemon user ID.
    @param gid Runtime daemon group ID.
    @return Absolute path to the generated config file.
    """
    directory = os.open(data_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptors = []
    temporary = ".daemon-" + secrets.token_hex(12)
    try:
        for name in (
            "daemon.json",
            "state.sqlite3",
            "state.sqlite3-wal",
            "state.sqlite3-shm",
            "state.sqlite3.lock",
            "state.sqlite3.firmware.lock",
        ):
            try:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
            except FileNotFoundError:
                continue
            descriptors.append(descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("App data must be regular files without hard links")
        for descriptor in descriptors:
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, 0o600)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(raw))
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), uid, gid)
        os.replace(temporary, "daemon.json", src_dir_fd=directory, dst_dir_fd=directory)
        os.fchown(directory, uid, gid)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return Path(data_path) / "daemon.json"


def main():
    """Prepare private data and serial group, then drop privileges permanently."""
    options = json.loads(Path("/data/options.json").read_text())
    raw = configuration(options)
    parse(raw)
    port = Path(raw["serial_port"])
    serial_gid = None
    try:
        metadata = port.stat()
        if not stat.S_ISCHR(metadata.st_mode):
            raise ValueError("Selected serial port is not a character device")
        serial_gid = metadata.st_gid
    except FileNotFoundError:
        # Keep the API and UI alive while the configured radio is disconnected.
        pass
    os.environ["LIVOLO_API_TOKEN"] = options["api_token"]
    os.umask(0o077)
    config_path = prepare_data(Path("/data"), raw)
    # When absent at boot, grant the platform dialout group so hotplug can recover.
    import grp

    groups = {serial_gid} if serial_gid is not None else set()
    for group in grp.getgrall():
        if group.gr_name in ("dialout", "uucp"):
            groups.add(group.gr_gid)
    os.setgroups(sorted(groups))
    os.setgid(10001)
    os.setuid(10001)
    os.execvp("livolod", ["livolod", "--config", str(config_path)])


if __name__ == "__main__":
    main()
