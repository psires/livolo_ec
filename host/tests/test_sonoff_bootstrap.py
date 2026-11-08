"""Exact Sonoff engineering profile tests; synthetic artifacts and no physical I/O."""

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.bootstrap_catalog import SONOFF_APPROVED_TRIAL, bootstrap_candidate
from livolod.bootstrap_controller import BootstrapController, BootstrapError
from livolod.bootstrap_runtime import BootstrapRuntime
from livolod.bootstrap_store import BootstrapStore
from livolod.bootstrap_worker import (
    BootstrapWorker,
    RadioOwnership,
    physical_selector,
    target_from_json,
    target_json,
)
from livolod.firmware_bundle import BundleError, select_candidate
from livolod.gecko_profiles import PROFILES, gecko_profile
from livolod.preserved_gecko import GeckoTarget, GeckoInstallError, check_loader
from livolod.zbgw04_provider import LinuxUSB, LoaderPolicy, PhysicalLease, select_target
import test_bootstrap_controller as controller_fixture
import test_bootstrap_runtime as runtime_fixture
import test_preserved_gecko as adapter_fixture
import test_zbgw04_provider as provider_fixture
from test_firmware_bundle import gbl, mg21_image

BOARD = "sonoff-zbdongle-e"
PROFILE = gecko_profile(BOARD)


def sonoff(target=None):
    """Construct synthetic exact-unit facts without using a real serial or topology.

    @param target Optional legacy fixture target whose endpoint should be retained.
    """
    target = target or GeckoTarget(
        "/dev/test-only",
        "devices/fixture/usb1/1-2",
        "generation",
        (188, 0),
        "00",
        "1.9.2",
    )
    return replace(
        target,
        board=BOARD,
        vid=0x10C4,
        pid=0xEA60,
        loader_version="2.4.2",
        serial="fixture-sonoff-unit-one",
    )


class ProfileTests(unittest.TestCase):
    def test_fixed_profile_and_legacy_serialization(self):
        """Old policies round-trip unchanged while profile definitions cannot be edited."""
        legacy = replace(
            sonoff(),
            board="zb-gw04-1v2",
            vid=0x1A86,
            pid=0x7523,
            loader_version="1.9.2",
            serial=None,
        )
        encoded = target_json(legacy)
        self.assertNotIn("serial", encoded)
        self.assertEqual(target_json(target_from_json(encoded)), encoded)
        self.assertIsNone(physical_selector(encoded)["serial"])
        self.assertEqual(target_from_json(target_json(sonoff())), sonoff())
        with self.assertRaises(FrozenInstanceError):
            PROFILE.loader_version = "1.9.2"
        with self.assertRaises(TypeError):
            PROFILES[BOARD] = gecko_profile()
        with self.assertRaises(TypeError):
            target_from_json(dict(encoded, usb_serial="alias-not-supported"))

    def test_profile_identity_and_loader_cannot_be_crossed(self):
        """Wrong USB, absent serial or another profile's loader fail before evidence use."""
        evidence = dict(
            kind="gecko",
            version="1.9.2",
            baud=115200,
            upload_supported=True,
            requires_signed=False,
            requires_encrypted=False,
        )
        for fields in (
            {"serial": None},
            {"serial": ""},
            {"serial": "bad\nserial"},
            {"serial": "ü"},
            {"serial": "x" * 129},
            {"vid": 0x1A86},
            {"pid": 0x7523},
            {"board": "unknown"},
            {"loader_version": "1.9.2"},
        ):
            with self.subTest(fields=fields), self.assertRaises(BundleError):
                check_loader(replace(sonoff(), **fields), evidence)

    def test_reconnect_same_unit_generation_only(self):
        """Normal reconnect permits renewed attachment, never a replacement serial."""
        with tempfile.TemporaryDirectory() as directory:
            selected = sonoff()
            inventory = Mock(return_value=(selected,))
            owner = RadioOwnership(
                dict(
                    profile=BOARD, serial_port=selected.port, lease_directory=directory
                ),
                inventory=inventory,
            )
            try:
                owner.acquire()
                owner.close()
                renewed = replace(selected, attachment="new-generation")
                inventory.return_value = (renewed,)
                self.assertEqual(owner.acquire(allow_reattach=True)[0], renewed)
                owner.close()
                inventory.return_value = (replace(renewed, serial="different-unit"),)
                with self.assertRaises(BundleError) as caught:
                    owner.acquire(allow_reattach=True)
                self.assertEqual(caught.exception.code, "target_changed")
            finally:
                owner.close()

    def test_saved_policy_wrong_serial_rejected_before_worker(self):
        """A private review for one unit cannot authorize a different serial."""
        target = sonoff()
        settings = dict(
            loader_policy=dict(target=target_json(replace(target, serial="other")))
        )
        owner = Mock()
        owner.acquire.return_value = (target, Mock())
        with patch("livolod.bootstrap_worker.backend_status", return_value=None):
            worker = BootstrapWorker(settings, owner, Mock())
        self.assertEqual(worker.precheck(), "loader_policy_required")
        self.assertIsNone(worker.child)


class SonoffProviderTests(unittest.TestCase):
    fails = provider_fixture.ProviderTests.fails
    loader = provider_fixture.ProviderTests.loader
    application = provider_fixture.ProviderTests.application

    def setUp(self):
        """Retarget the real provider fixture to a synthetic Sonoff exact-unit policy."""
        provider_fixture.ProviderTests.setUp(self)
        self.lease.close()
        self.target = sonoff(self.target)
        self.lease = PhysicalLease.acquire(self.target, self.root)
        self.addCleanup(self.lease.close)
        self.provider.target, self.provider.lease = self.target, self.lease
        self.inventory.return_value = (self.target,)
        self.provider.board_evidence = PROFILE.board_evidence
        self.provider.loader_policy = replace(self.policy, version="2.4.2")
        self.provider.modem_check = Mock()
        self.serial.data = provider_fixture.MENU.replace(b"1.9.2", b"2.04.02")
        self.serial.rewrite = lambda raw: raw.replace(b"zb-gw04-1v2", BOARD.encode())

    def test_normalized_loader_uses_newline_only(self):
        """Only two reviewed wire spellings qualify, with no numeric probe or reset."""
        for version in (b"2.04.02", b"2.4.2"):
            self.serial.data = provider_fixture.MENU.replace(b"1.9.2", version)
            result = self.loader()
            self.assertEqual(result["version"], "2.4.2")
            check_loader(self.target, result)
        self.assertEqual(self.serial.writes, [b"\n", b"\n"])
        self.assertTrue(all(event[1:3] == (True, True) for event in self.serial.events))
        self.assertTrue(self.serial.closed)

    def test_bad_modem_readback_stops_before_protocol_write(self):
        """A changed control-line state cannot silently fall back or send menu bytes."""
        self.provider.modem_check.side_effect = BundleError(
            "modem_state_mismatch", "test"
        )
        self.fails("modem_state_mismatch", self.loader)
        self.assertEqual(self.serial.writes, [])
        self.assertTrue(self.serial.closed)

    def test_unknown_menu_fails_without_fallback(self):
        """Partial, wrong-family, secure and unreviewed loaders never elicit digits."""
        menu = self.serial.data
        for data in (
            provider_fixture.MENU,
            menu.replace(b"2.04.02", b"2.04.03"),
            menu.replace(b"Gecko", b"Ember Serial"),
            b"encrypted images required\n" + menu,
            b"signed required\n" + menu,
            b"Gecko Bootloader v2.04.02",
            b"x" * 4097,
            b"ESN1\x00sniffer",
        ):
            self.serial.data = data
            self.serial.pending.clear()
            with self.subTest(data=data[:40]), self.assertRaises(BundleError):
                self.loader()
            self.assertTrue(self.serial.closed)
        self.assertTrue(all(raw == b"\n" for raw in self.serial.writes))

    def test_postflight_requires_three_matching_sonoff_replies(self):
        """The actual provider accepts only independently matched Sonoff application facts."""
        self.serial.data = None
        result = self.application()
        self.assertEqual(result["version"]["board"], BOARD)
        self.assertEqual(
            [raw.split()[1] for raw in self.serial.writes],
            [b"VERSION", b"CAPS", b"STATUS"],
        )
        self.assertEqual(len({raw.split()[0] for raw in self.serial.writes}), 3)
        for wrong in (b"zb-gw04-1v2", b"sniffer"):
            self.serial.rewrite = lambda raw: raw.replace(b"zb-gw04-1v2", wrong)
            self.fails("application_mismatch", self.application)

    def test_other_serial_or_board_assertion_cannot_open_uart(self):
        """Exact-unit identity and explicit board confirmation gate all loader I/O."""
        self.inventory.return_value = (replace(self.target, serial="another-unit"),)
        self.fails("target_changed", self.loader)
        self.inventory.return_value = (self.target,)
        self.provider.board_evidence = gecko_profile().board_evidence
        self.fails("unsupported_profile", self.loader)
        self.factory.assert_not_called()


class SonoffInventoryTests(unittest.TestCase):
    def setUp(self):
        """Use fake sysfs only, with the environment check explicitly injected."""
        provider_fixture.InventoryTests.setUp(self)
        (self.usb / "idVendor").write_text("10c4")
        (self.usb / "idProduct").write_text("ea60")
        (self.usb / "serial").write_text("fixture-sonoff-unit-one")
        self.reader.profile = PROFILE

    def test_serial_is_preserved_and_board_not_inferred(self):
        """Inventory selects configured transport while operator evidence remains separate."""
        target = self.reader.inventory()[0]
        self.assertEqual(target.serial, "fixture-sonoff-unit-one")
        self.assertEqual(target.board, BOARD)
        self.assertEqual(target.loader_version, "2.4.2")
        self.assertEqual(
            select_target(target.port, PROFILE.board_evidence, self.reader.inventory),
            target,
        )
        with self.assertRaises(BundleError):
            select_target(
                target.port, gecko_profile().board_evidence, self.reader.inventory
            )
        self.reader.profile = gecko_profile()
        self.assertEqual(self.reader.inventory(), ())

    def test_missing_serial_and_changed_sample_are_rejected(self):
        """Serial is mandatory and both inventory samples must describe the same unit."""
        (self.usb / "serial").unlink()
        with self.assertRaises(BundleError):
            self.reader.inventory()
        (self.usb / "serial").write_text("fixture-sonoff-unit-one")
        original = self.reader._snapshot
        calls = 0

        def changing(boot):
            """Replace the synthetic serial between metadata samples.

            @param boot Current fixture host boot identifier.
            """
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.usb / "serial").write_text("other-unit")
            return original(boot)

        with patch.object(self.reader, "_snapshot", side_effect=changing):
            with self.assertRaises(BundleError):
                self.reader.inventory()


class SonoffAdapterTests(unittest.TestCase):
    acknowledge = adapter_fixture.PreservedGeckoTests.acknowledge
    flash = adapter_fixture.PreservedGeckoTests.flash
    install = adapter_fixture.PreservedGeckoTests.install

    def setUp(self):
        """Use the existing guarded adapter fixture with board-specific synthetic bytes."""
        adapter_fixture.PreservedGeckoTests.setUp(self)
        self.target = sonoff(self.target)
        self.endpoint.update(
            identity=self.target.identity(), board_evidence=PROFILE.board_evidence
        )
        self.loader["version"] = self.target.loader_version
        for key in ("version", "caps"):
            self.application[key]["board"] = BOARD
        self.data = gbl(mg21_image(BOARD, FIRMWARE_COMPAT_VERSION))

    def test_application_only_install_has_correct_board_receipt(self):
        """A valid Sonoff transfer retains durable checkpoints and firmware-only success."""
        result = self.install()
        self.assertEqual(result["board"], BOARD)
        self.assertTrue(result["application_verified"])
        self.assertFalse(result["network_ready"])
        self.assertEqual(self.uploads, [self.data])
        self.assertFalse(any(path.exists() for path in self.paths))

    def test_cross_board_bytes_rejected_before_observation(self):
        """A qualified-looking ZB application cannot enter the Sonoff transfer path."""
        data = gbl(mg21_image("zb-gw04-1v2", FIRMWARE_COMPAT_VERSION))
        with self.assertRaises(GeckoInstallError) as caught:
            self.install(data=data, sha256=hashlib.sha256(data).hexdigest())
        self.assertFalse(caught.exception.mutation_possible)
        self.observe.assert_not_called()
        self.assertFalse(self.uploads)

    def test_wrong_postflight_retains_recovery(self):
        """Completed transfer never implies success when the independently queried board differs."""
        self.application["version"]["board"] = "zb-gw04-1v2"
        with self.assertRaises(GeckoInstallError) as caught:
            self.install()
        self.assertTrue(caught.exception.mutation_possible)
        self.assertEqual(caught.exception.recovery, "recovery_required")


class SonoffControllerTests(unittest.IsolatedAsyncioTestCase):
    submit = controller_fixture.ControllerTests.submit
    prepare = controller_fixture.ControllerTests.prepare
    asyncTearDown = controller_fixture.ControllerTests.asyncTearDown

    async def asyncSetUp(self):
        """Start with no normal gateway database, baseline or configured network."""
        await controller_fixture.ControllerTests.asyncSetUp(self)
        await self.controller.close()
        self.store.close()
        (self.root / "bootstrap.db").unlink()
        self.store = BootstrapStore(self.root / "bootstrap.db")
        controller_fixture.catalog(self.root / "catalog", BOARD)
        self.settings.update(profile=BOARD, network=None)
        self.runtime.enter.return_value = None
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )

    async def work(self, kind, job, candidate, binding, checkpoint):
        """Retain real controller transactions with injected Sonoff observations.

        @param kind Explicit admitted action.
        @param job Durable operation state.
        @param candidate Validated synthetic artifact.
        @param binding Previously reviewed target and loader.
        @param checkpoint Durable mutation acknowledgment.
        """
        if kind == "prepare":
            self.calls.append(kind)
            return dict(target=target_json(sonoff()), loader={"fixture": True})
        result = await controller_fixture.ControllerTests.work(
            self, kind, job, candidate, binding, checkpoint
        )
        result["board"] = BOARD
        return result

    async def test_first_start_does_not_open_gateway_or_create_network(self):
        """An unknown sniffer is not probed by normal startup or a read-only setup view."""
        await self.controller.resume_startup()
        self.assertEqual(self.controller.info()["profile"], BOARD)
        self.assertIsNone(self.controller.info()["expected_network_id"])
        self.assertIsNone(self.store.meta("baseline"))
        self.runtime.start_gateway.assert_not_awaited()
        self.worker.run.assert_not_awaited()

    async def test_cancel_without_baseline_revokes_old_plan_without_uart(self):
        """Cancellation ends setup truthfully and cannot leave old install consent reusable."""
        prepared = await self.prepare()
        old_session = self.controller.session
        result, payload, _ = await self.submit("cancel", "cancel", confirmed=True)
        self.assertEqual(result["stage"], "setup_cancelled")
        self.assertTrue(result["cancelled_unconfigured"])
        self.assertFalse(result["network_ready"])
        self.assertTrue(result["setup_required"])
        self.assertEqual(self.calls, ["prepare"])
        self.assertNotEqual(self.controller.session, old_session)
        self.assertIsNone(self.controller.inspection)
        self.assertEqual(await self.controller.submit("cancel", payload), result)
        rejected, _, _ = await self.submit(
            "install", "stale", plan_id=prepared["plan_id"], confirmed=True
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.calls, ["prepare"])
        self.runtime.handoff.assert_not_awaited()
        fresh = await self.prepare("fresh")
        self.assertEqual(fresh["status"], "confirmed")
        self.assertNotEqual(fresh["plan_id"], prepared["plan_id"])

    async def test_install_initialize_handoff_are_explicit(self):
        """Firmware success has no invented network; explicit initialization precedes handoff."""
        prepared = await self.prepare()
        self.assertEqual(prepared["plan"]["profile"], BOARD)
        installed, _, _ = await self.submit(
            "install", "install", plan_id=prepared["plan_id"], confirmed=True
        )
        self.assertTrue(installed["application_verified"])
        self.assertFalse(installed["network_ready"])
        self.runtime.handoff.assert_not_awaited()
        failed, _, _ = await self.submit(
            "handoff",
            "early",
            install_request_id="install",
            network=None,
            confirmed=True,
        )
        self.assertEqual(failed["error"], "network_required")
        initialized, _, _ = await self.submit(
            "initialize", "initialize", confirmed=True
        )
        ready, _, _ = await self.submit(
            "handoff",
            "handoff",
            install_request_id="install",
            network=None,
            confirmed=True,
        )
        self.assertTrue(ready["network_ready"])
        self.runtime.handoff.assert_awaited_once_with(initialized["network"])
        self.assertEqual(self.store.meta("normal_target")["serial"], sonoff().serial)

    async def test_plan_profile_target_and_instance_mismatches_rejected(self):
        """A stored plan cannot be consumed under different profile or instance bindings."""
        for key in ("profile", "target_id", "instance_id"):
            prepared = await self.prepare("prepare-" + key)
            plan, _ = self.store.plan(prepared["plan_id"])
            plan[key] = "wrong"
            with self.store.db:
                self.store.db.execute(
                    "UPDATE plans SET value=? WHERE id=?",
                    (json.dumps(plan), prepared["plan_id"]),
                )
            failed, _, _ = await self.submit(
                "install", "install-" + key, plan_id=prepared["plan_id"], confirmed=True
            )
            self.assertEqual(failed["error"], "plan_changed")
        self.assertEqual(self.calls, ["prepare"] * 3)

    async def test_tagged_or_populated_legacy_journal_cannot_change_profile(self):
        """Only an empty untagged journal can adopt a new board contract."""
        with self.assertRaises(ValueError):
            BootstrapController(
                self.store,
                dict(self.settings, profile="zb-gw04-1v2"),
                self.worker,
                self.runtime,
            )
        with self.store.db:
            self.store.db.execute("DELETE FROM meta WHERE key='profile'")
        with self.assertRaises(ValueError):
            BootstrapController(self.store, self.settings, self.worker, self.runtime)
        self.assertIsNone(self.store.meta("profile"))


class SonoffRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = runtime_fixture.RuntimeTests.asyncTearDown

    async def asyncSetUp(self):
        """Construct an isolated runtime without a real serial provider or state DB."""
        await runtime_fixture.RuntimeTests.asyncSetUp(self)
        self.settings["profile"] = BOARD
        self.runtime.profile = PROFILE

    async def test_first_handoff_requires_disabled_outputs_before_creating_state(self):
        """An invalid first-use output configuration fails before any gateway factory call."""
        self.runtime.raw["output_mode"] = "experimental_phase"
        self.runtime.gateway_factory = Mock()
        with self.assertRaises(BootstrapError) as caught:
            await self.runtime.handoff(self.network)
        self.assertEqual(caught.exception.code, "initial_outputs_must_be_disabled")
        self.runtime.gateway_factory.assert_not_called()
        self.assertFalse(Path(self.raw["database"]).exists())

    async def test_legacy_recovery_read_closes_connection_on_all_outcomes(self):
        """Admission cannot leak read-only SQLite handles on success or fenced failures."""
        Path(self.raw["database"]).touch()
        cases = (
            (None, None, None),
            ((json.dumps({"status": "unknown"}),), None, "existing_update_unresolved"),
            (("malformed-json",), None, "gateway_state_unavailable"),
            (
                None,
                sqlite3.OperationalError("fixture failure"),
                "gateway_state_unavailable",
            ),
        )
        for row, error, expected in cases:
            db = Mock()
            db.execute.return_value.fetchone.return_value = row
            db.execute.side_effect = error
            with patch(
                "livolod.bootstrap_runtime.sqlite3.connect", return_value=db
            ) as connect:
                self.assertEqual(self.runtime.admission_reason(), expected)
            self.assertIn("?mode=ro", connect.call_args.args[0])
            db.close.assert_called_once_with()

    async def test_first_handoff_disabled_outputs(self):
        """Normal handoff enables report reception while keeping output controls disabled."""
        await self.runtime.handoff(self.network)
        self.assertTrue(self.runtime.service.ready)
        self.assertEqual(self.runtime.service.config.output_mode, "disabled")
        self.assertTrue(self.runtime.service.gateway.armed)
        self.assertTrue(self.runtime.service.gateway.ack)
        self.assertIsNone(self.runtime.service.phase_client)

    async def test_restart_normal_target_refuses_another_serial_before_uart(self):
        """A persisted normal selector protects the unit after exact-generation pin removal."""
        target = sonoff()
        self.runtime.journal = Mock()
        self.runtime.journal.meta.side_effect = lambda key: (
            target_json(target) if key == "normal_target" else None
        )
        self.runtime.ownership = Mock()
        self.runtime.ownership.acquire.return_value = (
            replace(target, serial="other-unit"),
            Mock(),
        )
        with patch("livolod.bootstrap_runtime.Gateway.open") as opened:
            with self.assertRaises(ValueError):
                self.runtime._gateway()
        opened.assert_not_called()


class SonoffCatalogTests(unittest.TestCase):
    def test_private_trial_requires_own_evidence_and_never_promotes_ordinary_updates(
        self,
    ):
        """The explicit private exception stays board/evidence/hash-bound and unqualified."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller_fixture.catalog(root, BOARD)
            manifest = json.loads((root / "manifest.json").read_text())
            entry = manifest["artifacts"][0]
            entry.update(
                qualified=False, source_commit=SONOFF_APPROVED_TRIAL["source_commit"]
            )
            (root / "manifest.json").write_text(json.dumps(manifest))
            approval = dict(SONOFF_APPROVED_TRIAL, sha256=entry["sha256"])
            target = sonoff()
            policy = dict(
                schema=1,
                target=target_json(target),
                engineering=approval,
                loader=dict(
                    attachment=target.attachment,
                    evidence_id=approval["evidence_id"],
                    version="2.4.2",
                    requires_signed=False,
                    requires_encrypted=False,
                ),
            )
            settings = dict(profile=BOARD, catalog=directory, loader_policy=policy)
            with patch.dict(SONOFF_APPROVED_TRIAL, approval, clear=True):
                self.assertTrue(bootstrap_candidate(settings)["engineering_only"])
                with self.assertRaises(BundleError):
                    select_candidate(
                        root,
                        board=BOARD,
                        host_version=FIRMWARE_COMPAT_VERSION,
                        app_baudrate=460800,
                    )
                for field, value in (
                    ("evidence_id", "b437e13"),
                    ("board", "zb-gw04-1v2"),
                    ("sha256", "0" * 64),
                    ("source_commit", "0" * 40),
                ):
                    wrong = deepcopy(settings)
                    wrong["loader_policy"]["engineering"][field] = value
                    with self.subTest(field=field), self.assertRaises(BundleError):
                        bootstrap_candidate(wrong)
                for key, value in (("profile", "zb-gw04-1v2"), ("loader_policy", None)):
                    with self.subTest(key=key), self.assertRaises(BundleError):
                        bootstrap_candidate(dict(settings, **{key: value}))
