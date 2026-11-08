"""Synthetic provisioning target and worker; never import into deployed service code."""

from pathlib import Path

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.provisioning import PROFILE, ProvisioningModel
from livolod.provisioning_store import ProvisioningStore
from test_firmware_bundle import gbl, mg21_image

TOKEN = "isolated-model-credential-not-for-deployment"


class Clock:
    """Deterministic UTC and monotonic fixture clock."""

    def __init__(self):
        """Start away from zero to expose accidental relative/absolute time mixing."""
        self.now = 1_800_000_000.0

    def __call__(self):
        """Return seconds controlled exclusively by the test."""
        return self.now


class ProvisioningFixture:
    """Own a disposable journal, synthetic evidence and a model-only worker."""

    def __init__(self, root):
        """Create a fake ZB-GW04 1v2 with no corresponding physical device path.

        @param root Private test directory that the caller removes afterward.
        """
        self.path = Path(root) / "provisioning-model.sqlite3"
        self.clock = Clock()
        self.target = dict(
            target_id="fixture-radio",
            generation="attachment-1",
            board=PROFILE,
            transport_identity="fixture-topology-1",
            board_evidence="operator_confirmed",
            unique=True,
            ownership="exclusive",
            mode="stock",
            loader_supported=True,
        )
        self.artifact = dict(
            candidate_id="fixture-gbl",
            data=gbl(mg21_image(PROFILE, FIRMWARE_COMPAT_VERSION)),
        )
        self.transfers = 0
        self.checkpoints = []
        self.store = None
        self.restart()

    def restart(self):
        """Reopen only the journal and model; no fake transfer is ever replayed."""
        if self.store:
            self.store.close()
        self.store = ProvisioningStore(self.path)
        self.model = ProvisioningModel(
            self.store, TOKEN, self.target, self.artifact, self.clock, self.clock
        )

    def close(self):
        """Release the disposable state without invoking a worker."""
        self.store.close()

    def call(self, action, payload=None):
        """Invoke the exact direct-call facade used by the HA test presenter.

        @param action Info, targets, preflight, start or job.
        @param payload Exact action-specific JSON model request.
        """
        return self.model.request(TOKEN, action, payload)

    def replace_target(self, **fields):
        """Model an external target change, including disappearance or replacement.

        @param fields Exact evidence fields to replace in the server-side fixture.
        """
        self.target.update(fields)
        self.model.replace_fixture(self.target, self.artifact)

    def plan(self):
        """Select the sole synthetic target and immutable artifact for review."""
        return self.call(
            "preflight",
            dict(
                target_id=self.target["target_id"],
                candidate_id=self.artifact["candidate_id"],
            ),
        )

    def submission(self, plan, request_id="model-job-1"):
        """Construct an explicit simulated install confirmation without submitting it.

        @param plan Successful public plan data, including its service identity.
        @param request_id Stable client-generated idempotency handle.
        """
        return dict(
            instance_id=plan["instance_id"],
            plan_id=plan["plan_id"],
            request_id=request_id,
            confirmed=True,
        )

    def transfer(self, request_id="model-job-1"):
        """Simulate a transfer only after its durable possible-write checkpoint.

        @param request_id Explicitly confirmed pending job to model.
        """
        self.replace_target(mode="bootloader")
        checkpoint = self.model.before_transfer(request_id)
        durable = self.store.lookup(request_id)[1]
        assert durable == checkpoint and durable["mutation_possible"]
        self.checkpoints.append(durable)
        self.transfers += 1
        self.model.progress(request_id, 100)

    def finish(self, request_id="model-job-1", **overrides):
        """Model app return; never configure a network or create an HA entry.

        @param request_id Transferring model job.
        @param overrides Alter exact application facts for negative verification cases.
        """
        self.model.transferred(request_id)
        self.replace_target(mode="application")
        evidence = dict(
            board=PROFILE,
            fw_version=FIRMWARE_COMPAT_VERSION,
            proto="3",
            baud="460800",
            armed="0",
            ack="0",
        )
        evidence.update(overrides)
        self.model.verify(request_id, evidence)
