"""Upgrade orchestration preserves each release's compatibility and durable bytes."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_daemon_upgrade as upgrade


class UpgradeCompatibilityTests(unittest.TestCase):
    def run_check(self, *, previous_compatibility=None, mutate=None):
        """Exercise orchestration with inert installation and observable persistent state.

        @param previous_compatibility Optional previous target, including the legacy default.
        @param mutate Optional corruption injection: catalog or registry.
        @return Evidence and observed per-phase compatibility targets.
        """
        phases = []
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "previous.whl"
            new = Path(directory) / "candidate.whl"
            old.write_bytes(b"old")
            new.write_bytes(b"new")

            def installed(command, **kwargs):
                """Report the fake package selected by the current installation phase.

                @param command Wheel metadata or console identity query.
                @param kwargs Ignored subprocess controls.
                @return Correct package/report text for the orchestrated phase.
                """
                if "--version-info" in command:
                    return json.dumps(
                        dict(
                            daemon_version="0.15.0",
                            firmware_compat_version="0.14",
                            api_version=1,
                            uart_protocol=3,
                        )
                    )
                return ("0.15.0" if len(phases) == 1 else "0.13") + "\n"

            def state(environment, root, expected, compatibility, seed=False):
                """Record phase targets and optionally corrupt immutable persistence.

                @param environment Unused inert environment path.
                @param root Shared persistent test catalog.
                @param expected Installed software expectation.
                @param compatibility Phase-specific radio target.
                @param seed Whether this is the original installation.
                @return Stable serialized records unless corruption is requested.
                """
                phases.append((expected, compatibility, seed))
                manifest = root / "catalog/manifest.json"
                self.assertEqual(
                    json.loads(manifest.read_bytes())["host_version"],
                    previous_compatibility or "0.14",
                )
                if mutate == "catalog" and len(phases) == 2:
                    manifest.write_bytes(manifest.read_bytes() + b"\n")
                return {
                    "devices": (
                        "changed"
                        if mutate == "registry" and len(phases) == 2
                        else "original"
                    ),
                    "request": "original",
                }

            with (
                patch.object(upgrade, "wheel_version", side_effect=["0.13", "0.15.0"]),
                patch.object(upgrade.venv.EnvBuilder, "create"),
                patch.object(upgrade.subprocess, "run"),
                patch.object(upgrade.subprocess, "check_output", side_effect=installed),
                patch.object(upgrade, "state_check", side_effect=state),
                patch.object(
                    upgrade, "catalog_check", return_value="checked"
                ) as catalog,
            ):
                result = upgrade.check(
                    old, new, "0.14", previous_compatibility=previous_compatibility
                )
                self.assertEqual(
                    [call.args[2] for call in catalog.call_args_list],
                    [phase[1] for phase in phases],
                )
            return result, phases

    def test_previous_upgrade_and_rollback_use_separate_targets(self):
        """The candidate radio target never contaminates baseline or rollback expectations."""
        result, phases = self.run_check(previous_compatibility="0.13")
        self.assertEqual(
            phases,
            [
                ("0.13", "0.13", True),
                ("0.15.0", "0.14", False),
                ("0.13", "0.13", False),
            ],
        )
        self.assertEqual(result["previous_firmware_compat_version"], "0.13")
        self.assertEqual(result["firmware_compat_version"], "0.14")

    def test_old_callers_keep_same_compatibility_default(self):
        """Omitting the new argument preserves the original same-target invocation."""
        result, phases = self.run_check()
        self.assertEqual([phase[1] for phase in phases], ["0.14"] * 3)
        self.assertEqual(result["previous_firmware_compat_version"], "0.14")

    def test_changed_catalog_or_registry_fails_the_gate(self):
        """An upgrade cannot pass by rewriting old catalog bytes or durable device records."""
        for mutate in ("catalog", "registry"):
            with self.subTest(mutate=mutate), self.assertRaises(AssertionError):
                self.run_check(previous_compatibility="0.13", mutate=mutate)


if __name__ == "__main__":
    unittest.main()
