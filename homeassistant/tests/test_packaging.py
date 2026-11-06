"""Validate Supervisor configuration translation without touching /data or serial."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from livolod.config import parse

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "livolo_app", ROOT / "homeassistant/app/run.py"
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_release_bundles_client_and_license(tmp_path):
    """Release archives retain the client dependency and redistribution license.

    @param tmp_path Isolated output directory for the existing release builder.
    """
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "homeassistant/build_release.py"),
            "--app",
            "--output",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    license_text = (ROOT / "LICENSE").read_bytes()
    archive = next(tmp_path.glob("livolo-ec-ha-*.zip"))
    with zipfile.ZipFile(archive) as bundle:
        prefix = "custom_components/livolo_ec/"
        assert bundle.read(prefix + "LICENSE") == license_text
        assert (
            bundle.read(prefix + "_client/__init__.py")
            == (ROOT / "client/livolo_ec_client/__init__.py").read_bytes()
        )
    context = tmp_path / "ha-addons/livolo_ec"
    assert (context / "LICENSE").read_bytes() == license_text
    assert (context / "host/LICENSE").read_bytes() == license_text
    assert (ROOT / "client/LICENSE").read_bytes() == license_text
    for relative in (
        "host/docker/requirements-firmware.txt",
        "host/livolod/firmware/manifest.json",
    ):
        assert (context / relative).read_bytes() == (ROOT / relative).read_bytes()


def test_options_keep_secrets_out_of_daemon_json(tmp_path):
    """The app starts broker-free with ingress and does not persist API tokens in daemon JSON.

    @param tmp_path Private test directory.
    """
    raw = app.configuration(
        {
            "gateway": "10203040",
            "tag": "42",
            "serial_port": "/dev/serial/by-id/example",
            "serial_baudrate": 460800,
            "api_token": "s" * 32,
            "ha_origin": "https://ha.example",
        },
        tmp_path,
    )
    config = parse(raw)
    assert config.mqtt == {"enabled": False}
    assert config.database == str(tmp_path / "state.sqlite3")
    assert config.web["ingress"]["enabled"]
    assert "firmware_bundle_dir" not in raw
    assert "s" * 32 not in str(raw)


@pytest.mark.parametrize("catalog", ["bundled", "local"])
@pytest.mark.parametrize("invalid_manifest", [False, True])
def test_native_only_options_keep_service_configurable(
    tmp_path, catalog, invalid_manifest
):
    """Web and an absent or invalid local catalog cannot prevent native configuration.

    @param tmp_path Private app data directory.
    @param catalog Bundled or fixed persistent catalog selection.
    @param invalid_manifest Whether to create a malformed operator manifest.
    """
    if invalid_manifest:
        (tmp_path / "firmware").mkdir()
        (tmp_path / "firmware/manifest.json").write_text("invalid")
    raw = app.configuration(
        {
            "gateway": "10203040",
            "tag": "42",
            "serial_port": "/dev/serial/by-id/example",
            "serial_baudrate": 460800,
            "api_token": "s" * 32,
            "web_enabled": False,
            "firmware_catalog": catalog,
        },
        tmp_path,
    )
    config = parse(raw)
    assert not config.web["enabled"]
    assert "ingress" not in config.web
    assert config.api["enabled"] and config.api["port"] == 8090
    assert config.api["token_env"] == "LIVOLO_API_TOKEN"
    assert config.database == str(tmp_path / "state.sqlite3")
    assert config.gateway == "10203040" and config.tag == "42"
    assert "s" * 32 not in str(raw)
    if catalog == "local":
        assert config.firmware_bundle_dir == str(tmp_path / "firmware")
    else:
        assert "firmware_bundle_dir" not in raw


@pytest.mark.parametrize(
    "option,value",
    [
        ("web_enabled", "false"),
        ("web_enabled", 0),
        ("web_enabled", None),
        ("firmware_catalog", "/tmp/firmware"),
        ("firmware_catalog", "https://example.invalid/catalog"),
        ("firmware_catalog", None),
    ],
)
def test_invalid_optional_options_rejected(option, value):
    """Options cannot silently enable Web or select arbitrary catalog locations.

    @param option Supervisor option to validate.
    @param value Invalid submitted setting.
    """
    with pytest.raises(ValueError, match=option):
        app.configuration({"api_token": "s" * 32, option: value})


def test_short_api_token_rejected():
    """A missing/weak installation token cannot expose the machine API."""
    with pytest.raises(ValueError):
        app.configuration({"api_token": "short"})


@pytest.mark.parametrize(
    "name",
    [
        "daemon.json",
        "state.sqlite3",
        "state.sqlite3.lock",
        "state.sqlite3.firmware.lock",
    ],
)
def test_privileged_bootstrap_rejects_symlinks(tmp_path, name):
    """Startup never follows data links to overwrite or chown outside files.

    @param tmp_path Isolated test directory.
    @param name Data filename offered as a symlink.
    """
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("preserve")
    (data / name).symlink_to(outside)
    with pytest.raises(OSError):
        app.prepare_data(data, {"network": "test"}, os.getuid(), os.getgid())
    assert outside.read_text() == "preserve"


def test_privileged_bootstrap_rejects_hardlinks(tmp_path):
    """A linked database cannot cause startup to change another file's ownership.

    @param tmp_path Isolated test directory.
    """
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("preserve")
    os.link(outside, data / "state.sqlite3")
    with pytest.raises(ValueError):
        app.prepare_data(data, {}, os.getuid(), os.getgid())
    assert outside.read_text() == "preserve"


def test_privileged_bootstrap_writes_private_config(tmp_path):
    """Normal startup atomically creates daemon config without exposing secrets.

    @param tmp_path Isolated test directory.
    """
    path = app.prepare_data(tmp_path, {"gateway": "10203040"}, os.getuid(), os.getgid())
    assert json.loads(path.read_text()) == {"gateway": "10203040"}
    assert path.stat().st_mode & 0o777 == 0o600
