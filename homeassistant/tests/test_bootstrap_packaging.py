"""Opt-in app settings, guarded paths and saved-network startup without real hardware."""

import json
import os
from pathlib import Path

import pytest

from livolod.bootstrap_controller import BootstrapController
from livolod.bootstrap_runtime import BootstrapRuntime
from livolod.bootstrap_store import BootstrapStore
from livolod.storage import Store
from unittest.mock import AsyncMock, Mock
from test_packaging import app


def options(**overrides):
    """Return a native-only fixture with an explicitly chosen setup profile.

    @param overrides Specific malformed or initial-mode settings to exercise.
    """
    return dict(
        serial_port="/dev/test-only",
        serial_baudrate=460800,
        gateway="",
        tag="",
        api_token="x" * 32,
        web_enabled=False,
        radio_setup_profile="zb-gw04-1v2",
        radio_mode="bootstrap",
        **overrides,
    )


def test_disabled_profile_retains_normal_config_validation(tmp_path):
    """Default deployments cannot create a fake network from empty initial options.

    @param tmp_path Private test state directory.
    """
    value = options()
    value.update(radio_setup_profile="disabled", radio_mode="gateway")
    raw = app.configuration(value, tmp_path)
    assert app.bootstrap_settings(value, raw, tmp_path) is None
    from livolod.config import parse

    with pytest.raises(ValueError):
        parse(raw)


def test_setup_settings_never_persist_token_or_create_network(tmp_path):
    """Selecting bootstrap does not create a gateway DB or select an implicit identity.

    @param tmp_path Private test state directory.
    """
    value = options()
    raw = app.configuration(value, tmp_path)
    settings = app.bootstrap_settings(value, raw, tmp_path)
    assert raw["network"] is None
    assert raw["output_mode"] == "disabled" and raw["devices"] == []
    assert settings["loader_policy"] is None
    assert not Path(raw["database"]).exists()
    assert value["api_token"] not in json.dumps(dict(daemon=raw, bootstrap=settings))


async def test_blank_options_restart_uses_saved_journal_network(tmp_path):
    """An initialized hub restarts using the same identity despite blank Supervisor seeds.

    @param tmp_path Private journal and gateway database directory.
    """
    value = options()
    raw = app.configuration(value, tmp_path)
    settings = app.bootstrap_settings(value, raw, tmp_path)
    network = dict(gateway="10203040", tag="42")
    journal = BootstrapStore(settings["journal"])
    journal.set_meta("network", network)
    journal.set_meta("mode", "gateway")
    journal.close()
    store = Store(raw["database"], "10203040:42")
    store.close()
    # Privileged wrapper accepts the existing durable network; runtime validates it.
    settings = app.bootstrap_settings(
        value, app.configuration(value, tmp_path), tmp_path
    )
    journal = BootstrapStore(settings["journal"])
    runtime = BootstrapRuntime(raw, settings, gateway_factory=Mock())
    runtime.start_gateway = AsyncMock()
    worker = Mock(blocked_reason=None)
    controller = BootstrapController(journal, settings, worker, runtime)
    try:
        await controller.resume_startup()
        runtime.start_gateway.assert_awaited_once_with(network)
        assert controller.info()["expected_network_id"] == "10203040:42"
    finally:
        await controller.close()
        journal.close()


async def test_unrelated_network_database_is_not_rebound(tmp_path):
    """A saved setup identity cannot silently take over a different gateway database.

    @param tmp_path Private test state directory.
    """
    value = options()
    raw = app.configuration(value, tmp_path)
    settings = app.bootstrap_settings(value, raw, tmp_path)
    store = Store(raw["database"], "aabbccdd:54")
    store.close()
    runtime = BootstrapRuntime(raw, settings, gateway_factory=Mock())
    with pytest.raises(ValueError, match="another network"):
        await runtime.start_gateway(dict(gateway="10203040", tag="42"))
    assert runtime.service is None and runtime.quiesced


def test_gateway_database_cannot_masquerade_as_setup_journal(tmp_path):
    """An unrelated preexisting DB is rejected before controller startup.

    @param tmp_path Private test state directory.
    """
    path = tmp_path / "bootstrap.sqlite3"
    store = Store(str(path), "10203040:42")
    store.close()
    with pytest.raises(ValueError, match="physical bootstrap"):
        BootstrapStore(path)


def test_shared_leases_reject_symlink_and_unsafe_permissions(tmp_path):
    """Privileged preparation cannot redirect locks into another directory.

    @param tmp_path Private test directory.
    """
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        app.prepare_shared_leases(link, os.getuid(), os.getgid())
    target.chmod(0o777)
    with pytest.raises(ValueError):
        app.prepare_shared_leases(target, os.getuid(), os.getgid())
    target.chmod(0o700)
    app.prepare_shared_leases(target, os.getuid(), os.getgid())


def test_engineering_catalog_is_separate_from_ordinary_public_catalog(tmp_path):
    """The app routes protected engineering evidence to a fixed private catalog only.

    @param tmp_path Private app data and policy directory.
    """
    value = options()
    raw = app.configuration(value, tmp_path)
    policy = dict(schema=1, target={}, loader={}, engineering={"fixture": True})
    (tmp_path / "bootstrap-policy.json").write_text(json.dumps(policy))
    (tmp_path / "bootstrap-policy.json").chmod(0o600)
    settings = app.bootstrap_settings(value, raw, tmp_path)
    assert settings["catalog"] == str(tmp_path / "bootstrap-firmware")
    assert "firmware_bundle_dir" not in raw
