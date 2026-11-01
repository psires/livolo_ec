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
    assert "s" * 32 not in str(raw)


def test_short_api_token_rejected():
    """A missing/weak installation token cannot expose the machine API."""
    with pytest.raises(ValueError):
        app.configuration({"api_token": "short"})


@pytest.mark.parametrize("name", ["daemon.json", "state.sqlite3", "state.sqlite3.lock"])
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
