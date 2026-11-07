"""Channel exports pin source and recipe, and cannot publish preview or changed files."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "6fe41cbdc909a6497776202b3eeae088655b9908"


def module(path, name):
    """Load a standalone packaging helper without shadowing Home Assistant itself.

    @param path Absolute helper source path.
    @param name Unique module name used for this test.
    @return Loaded helper module.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def exporter(monkeypatch):
    """Model a committed packaging recipe while keeping exact historical source reads.

    @param monkeypatch Scoped replacement of only the Git cleanliness response.
    """
    result = module(ROOT / "homeassistant/export_channel.py", "channel_export")
    original = subprocess.check_output

    def git_output(command, **kwargs):
        """Simulate a clean recipe without replacing archive or commit reads.

        @param command Git invocation from the exporter.
        @param kwargs Original subprocess keyword arguments.
        """
        if command[:3] == ["git", "status", "--porcelain"]:
            return b""
        return original(command, **kwargs)

    monkeypatch.setattr(result.subprocess, "check_output", git_output)
    return result


def test_channel_zip_is_complete_and_reproducible(tmp_path, exporter):
    """A pinned baseline produces a flat complete ZIP with independent source/recipe evidence.

    @param tmp_path New generated repository directory parent.
    @param exporter Export helper with a committed-recipe fixture.
    """
    output = exporter.export(BASELINE, "0.2.0", tmp_path / "repository")
    provenance = json.loads((output / "source.json").read_text())
    assert provenance["source_commit"] == BASELINE
    assert len(provenance["packaging_commit"]) == 40
    assert not provenance["preview"]
    readme = (output / "README.md").read_text()
    assert "docs/PAIRING.md" not in readme
    assert "<!-- pairing-guide -->" not in readme
    assert (
        "homeassistant/channel/workflows/release.yml" in provenance["packaging_files"]
    )
    assert "custom_components/livolo_ec/brand/icon.png" in provenance["packaging_files"]
    builder = module(output / "scripts/build_release.py", "channel_builder")
    archive = builder.build(output, "0.2.0")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert json.loads(bundle.read("manifest.json"))["version"] == "0.2.0"
        assert bundle.read("_client/__init__.py")
        assert bundle.read("LICENSE") == (ROOT / "LICENSE").read_bytes()
        assert bundle.read("brand/icon.png").startswith(b"\x89PNG")
        assert not any(
            name.startswith("custom_components/") for name in bundle.namelist()
        )
    assert hashlib.sha256(builder.build(output).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("change", ["tag", "component", "recipe", "preview"])
def test_release_rejects_unreviewed_inputs(tmp_path, exporter, change):
    """Tag mismatches, changed code/recipe and preview markers cannot become releases.

    @param tmp_path Isolated generated distribution.
    @param exporter Export helper with a committed-recipe fixture.
    @param change Invalid condition introduced after the export.
    """
    output = exporter.export(BASELINE, "0.2.0", tmp_path / "repository")
    builder = module(output / "scripts/build_release.py", "channel_builder")
    if change == "component":
        (output / "custom_components/livolo_ec/switch.py").write_text("# changed\n")
    elif change == "recipe":
        (output / ".github/workflows/release.yml").write_text("# changed\n")
    elif change == "preview":
        path = output / "source.json"
        provenance = json.loads(path.read_text())
        provenance["preview"] = True
        path.write_text(json.dumps(provenance))
    with pytest.raises(ValueError):
        builder.build(output, "0.2.1" if change == "tag" else "0.2.0")


def test_working_preview_cannot_be_published(tmp_path):
    """An explicitly requested working-tree preview always blocks release packaging.

    @param tmp_path Isolated preview directory.
    """
    exporter = module(ROOT / "homeassistant/export_channel.py", "preview_export")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    version = json.loads(
        (ROOT / "custom_components/livolo_ec/manifest.json").read_text()
    )["version"]
    output = exporter.export(commit, version, tmp_path / "preview", preview=True)
    component = output / "custom_components/livolo_ec"
    for name in ("button.py", "commissioning.py"):
        assert (component / name).read_bytes() == (
            ROOT / "custom_components/livolo_ec" / name
        ).read_bytes()
    assert (component / "_client/__init__.py").read_bytes() == (
        ROOT / "client/livolo_ec_client/__init__.py"
    ).read_bytes()
    assert "non-release preview" in (output / "README.md").read_text()
    builder = module(output / "scripts/build_release.py", "preview_builder")
    with pytest.raises(ValueError, match="non-preview"):
        builder.build(output)


def test_guide_link_uses_selected_source_not_packaging_head(
    tmp_path, exporter, monkeypatch
):
    """A guide-bearing source uses its own SHA even with a newer packaging recipe.

    @param tmp_path Isolated generated distribution parent.
    @param exporter Export helper with a committed-recipe fixture.
    @param monkeypatch Scoped simulation of the guide existing at the source SHA.
    """
    original = subprocess.run

    def guide_exists(command, **kwargs):
        """Model a guide-bearing revision without changing historical application code.

        @param command Git existence check or unrelated subprocess invocation.
        @param kwargs Original subprocess options.
        @return Successful guide check or the real unrelated subprocess result.
        """
        if command == ["git", "cat-file", "-e", BASELINE + ":docs/PAIRING.md"]:
            return subprocess.CompletedProcess(command, 0)
        return original(command, **kwargs)

    monkeypatch.setattr(exporter.subprocess, "run", guide_exists)
    output = exporter.export(BASELINE, "0.2.0", tmp_path / "guide-channel")
    readme = (output / "README.md").read_text()
    assert f"/blob/{BASELINE}/docs/PAIRING.md" in readme
    assert "/blob/main/docs/PAIRING.md" not in readme
    provenance = json.loads((output / "source.json").read_text())
    assert (
        provenance["distribution_files"]["README.md"]
        == hashlib.sha256((output / "README.md").read_bytes()).hexdigest()
    )


def test_disposable_supervisor_catalog_is_radio_free(tmp_path):
    """Both image versions use the same isolated app identity and cannot map the radio.

    @param tmp_path Generated catalog parent directory.
    """
    probe = module(ROOT / "homeassistant/stage_supervisor_probe.py", "supervisor_probe")
    configs = []
    for version in ("0.2.0", "0.2.1"):
        output = probe.stage(version, tmp_path / version)
        config = yaml.safe_load((output / "homeassistant/app/config.yaml").read_text())
        assert config["version"] == version
        assert not set(config) & {
            "uart",
            "devices",
            "ports",
            "host_network",
            "ingress",
            "privileged",
        }
        assert config["boot"] == "manual"
        assert config["options"]["output_mode"] == "disabled"
        assert config["options"]["api_token"] == ""
        assert (
            config["schema"]["serial_port"]
            == "match(^/dev/livolo-channel-test-no-radio$)"
        )
        config.pop("version")
        configs.append(config)
    assert configs[0] == configs[1]
