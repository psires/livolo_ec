"""Exercise the experimental publisher's embedded request resolver without Actions."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/container-experimental-amd64.yml"


class RequestTests(unittest.TestCase):
    def setUp(self):
        """Extract the exact workflow script and isolate its temporary output."""
        text = WORKFLOW.read_text()
        body = text.split("          python3 - <<'PYCODE'\n", 1)[1].split(
            "          PYCODE\n", 1
        )[0]
        self.script = "\n".join(line[10:] for line in body.splitlines())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.output = self.path / "output"
        self.env = dict(
            os.environ,
            EVENT="workflow_dispatch",
            RUN_ATTEMPT="1",
            INPUT_PHASE="build",
            INPUT_DIGEST="",
            INPUT_SOURCE="",
            INPUT_ZIP="",
            GITHUB_OUTPUT=str(self.output),
        )

    def run_request(self, **changes):
        """Execute the resolver with supplied environment changes in the fixture repo.

        @param changes Environment values for this simulated workflow request.
        @return Completed subprocess including its exit status.
        """
        return subprocess.run(
            [sys.executable, "-c", self.script],
            cwd=self.path,
            env=dict(self.env, **changes),
            capture_output=True,
            text=True,
        )

    def test_dispatch_outputs_separate_lines_and_rejects_injection(self):
        """Valid requests write proper output lines and reject injected newlines."""
        self.assertEqual(self.run_request().returncode, 0)
        self.assertEqual(
            self.output.read_text().splitlines(),
            ["phase=build", "candidate_digest=", "source_url=", "zip_url="],
        )
        self.output.unlink()
        result = self.run_request(
            INPUT_PHASE="promote",
            INPUT_DIGEST="sha256:" + "a" * 64,
            INPUT_SOURCE="https://example.test/\nevil=yes",
            INPUT_ZIP="https://example.test/zip",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_push_request_and_rerun_guard(self):
        """Only the single reviewed request commit can initiate a non-rerun push."""

        def git(*args):
            """Run Git inside the synthetic repository.

            @param args Git command arguments.
            """
            subprocess.run(
                ["git", *args], cwd=self.path, check=True, capture_output=True
            )

        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "Fixture")
        (self.path / "seed").write_text("seed")
        git("add", "seed")
        git("commit", "-qm", "seed")
        request = (
            self.path / "docs/verification/experimental-amd64-publication-request.json"
        )
        request.parent.mkdir(parents=True)
        request.write_text(
            '{"phase":"build","candidate_digest":"","source_url":"","zip_url":"","nonce":"fixture-01"}'
        )
        git("add", str(request.relative_to(self.path)))
        git("commit", "-qm", "request")
        self.assertEqual(self.run_request(EVENT="push").returncode, 0)
        self.output.unlink()
        self.assertNotEqual(
            self.run_request(EVENT="push", RUN_ATTEMPT="2").returncode, 0
        )
        self.assertFalse(self.output.exists())
        request.write_text(request.read_text().replace("fixture-01", "fixture-02"))
        (self.path / "seed").write_text("changed")
        git("add", "seed", str(request.relative_to(self.path)))
        git("commit", "-qm", "mixed")
        self.assertNotEqual(self.run_request(EVENT="push").returncode, 0)

    def test_promotion_requires_exact_digest_and_https_assets(self):
        """Promotion rejects malformed digests or assets without HTTPS."""
        valid = dict(
            INPUT_PHASE="promote",
            INPUT_DIGEST="sha256:" + "a" * 64,
            INPUT_SOURCE="https://example.test/source",
            INPUT_ZIP="https://example.test/zip",
        )
        self.assertEqual(self.run_request(**valid).returncode, 0)
        for change in (
            dict(INPUT_DIGEST="sha256:bad"),
            dict(INPUT_ZIP="http://example.test/zip"),
            dict(INPUT_SOURCE="https://example.test/\rX=y"),
        ):
            self.assertNotEqual(self.run_request(**dict(valid, **change)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
