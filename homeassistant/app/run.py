"""Translate Supervisor options, grant serial access, then exec the non-root daemon."""

import json
import os
from pathlib import Path
import stat
import secrets
import sys

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
    profile = options.get("radio_setup_profile", "disabled")
    mode = options.get("radio_mode", "gateway")
    if profile not in ("disabled", "zb-gw04-1v2") or mode not in (
        "gateway",
        "bootstrap",
    ):
        raise ValueError("Invalid radio setup profile or mode")
    if profile == "disabled" and mode != "gateway":
        raise ValueError("Select an explicit radio setup profile before bootstrap mode")
    selected = {"gateway": options["gateway"].lower(), "tag": options["tag"].lower()}
    if not selected["gateway"] and not selected["tag"] and profile != "disabled":
        selected = None
        if mode != "bootstrap":
            raise ValueError("An unconfigured radio requires bootstrap mode")
    raw = {
        "network": selected,
        "serial_port": options["serial_port"],
        "serial_baudrate": int(options["serial_baudrate"]),
        "database": str(data_path / "state.sqlite3"),
        "mqtt": {"enabled": False},
        "devices": [],
        "output_mode": (
            options.get("output_mode", "experimental_phase") if selected else "disabled"
        ),
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


def prepare_data(data_path, raw, uid=10001, gid=10001, filename="daemon.json"):
    """Prepare app-owned files without following links during privileged startup.

    @param data_path Existing private data directory.
    @param raw Validated non-secret daemon configuration.
    @param uid Runtime daemon user ID.
    @param gid Runtime daemon group ID.
    @param filename Fixed generated config name, selected by the wrapper only.
    @return Absolute path to the generated config file.
    """
    if filename not in ("daemon.json", "bootstrap.json"):
        raise ValueError("Invalid generated config name")
    directory = os.open(data_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptors = []
    temporary = ".daemon-" + secrets.token_hex(12)
    try:
        for name in (
            "daemon.json",
            "bootstrap.json",
            "bootstrap.sqlite3",
            "bootstrap.sqlite3.lock",
            "bootstrap.sqlite3-journal",
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
        os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        os.fchown(directory, uid, gid)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return Path(data_path) / filename


def bootstrap_settings(options, raw, data_path=Path("/data")):
    """Build opt-in server settings; no HTTP field can select a path or security policy.

    @param options Supervisor-controlled explicit profile and initial mode.
    @param raw Non-secret daemon configuration with optional accepted network.
    @param data_path Existing persistent app data directory.
    """
    if options.get("radio_setup_profile", "disabled") == "disabled":
        return None
    if raw["serial_baudrate"] != 460800 or not raw["serial_port"].startswith("/dev/"):
        raise ValueError("ZB-GW04 setup requires a selected /dev serial port at 460800")
    if raw["network"] is not None:
        parse(raw)
    elif (
        Path(raw["database"]).exists()
        and not (data_path / "bootstrap.sqlite3").exists()
    ):
        raise ValueError("Existing gateway state requires its original gateway and tag")
    policy = None
    try:
        descriptor = os.open(
            data_path / "bootstrap-policy.json", os.O_RDONLY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        pass
    else:
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o022
            ):
                raise ValueError("Bootstrap policy must be a protected regular file")
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError("Oversized bootstrap policy")
        policy = json.loads(data)
        if (
            not isinstance(policy, dict)
            or not {"schema", "target", "loader"} <= set(policy)
            or set(policy) - {"schema", "target", "loader", "engineering"}
            or policy["schema"] != 1
        ):
            raise ValueError("Invalid bootstrap policy")
    return dict(
        serial_port=os.path.realpath(raw["serial_port"]),
        port_aliases=list(
            dict.fromkeys((raw["serial_port"], os.path.realpath(raw["serial_port"])))
        ),
        database=raw["database"],
        journal=str(data_path / "bootstrap.sqlite3"),
        catalog=(
            str(data_path / "bootstrap-firmware")
            if policy and policy.get("engineering") is not None
            else raw.get(
                "firmware_bundle_dir",
                str(Path(__import__("livolod").__file__).parent / "firmware"),
            )
        ),
        lease_directory="/share/livolo-radio-locks",
        loader_policy=policy,
        mode=options.get("radio_mode", "gateway"),
        network=raw["network"],
    )


def prepare_shared_leases(path, uid=10001, gid=10001):
    """Prepare the host-shared cooperative guard directory without following links.

    @param path Fixed shared lease directory under the Supervisor share mount.
    @param uid Runtime owner ID used consistently by cooperating deployments.
    @param gid Runtime group ID.
    """
    path = Path(path)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid not in (0, uid) or metadata.st_mode & 0o077:
            raise ValueError(
                "Shared radio lease directory must be private and owned by Livolo"
            )
        os.fchown(descriptor, uid, gid)
    finally:
        os.close(descriptor)


def main():
    """Prepare private data and serial group, then drop privileges permanently."""
    options = json.loads(Path("/data/options.json").read_text())
    raw = configuration(options)
    settings = bootstrap_settings(options, raw)
    if settings is None:
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
    if settings is None:
        config_path = prepare_data(Path("/data"), raw)
    else:
        prepare_shared_leases(settings["lease_directory"])
        config_path = prepare_data(
            Path("/data"),
            {"daemon": raw, "bootstrap": settings},
            filename="bootstrap.json",
        )
    # When absent at boot, grant the platform dialout group so hotplug can recover.
    import grp

    groups = {serial_gid} if serial_gid is not None else set()
    for group in grp.getgrall():
        if group.gr_name in ("dialout", "uucp"):
            groups.add(group.gr_gid)
    os.setgroups(sorted(groups))
    os.setgid(10001)
    os.setuid(10001)
    if settings is None:
        os.execvp("livolod", ["livolod", "--config", str(config_path)])
    else:
        os.execvp(
            sys.executable,
            [
                sys.executable,
                "-m",
                "livolod.bootstrap_runtime",
                "--config",
                str(config_path),
            ],
        )


if __name__ == "__main__":
    main()
