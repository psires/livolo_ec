"""Exercise guided setup against the isolated model, never a production service or radio."""

from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from provisioning_flow_fixture import GuidedSetupPrototype


@pytest.fixture
def provisioning(tmp_path, monkeypatch):
    """Reuse Unix's synthetic artifact and direct-call service without hardware helpers.

    @param tmp_path Disposable directory for the independent prototype journal.
    @param monkeypatch Temporary import path for the shared host test fixtures.
    """
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "host/tests"))
    module = importlib.import_module("provisioning_fixture")
    fixture = module.ProvisioningFixture(tmp_path)
    yield fixture
    fixture.close()


def review(flow):
    """Select the model's sole advertised choice and stop at explicit review.

    @param flow Isolated guided-setup prototype.
    """
    state = flow.begin()
    choices = state["choices"]
    return flow.review(choices["targets"][0]["target_id"], choices["candidate_id"])


async def test_guided_setup_requires_confirmation_and_separate_hub_setup(
    hass, provisioning
):
    """Firmware verification never creates a hub or claims network readiness.

    @param hass Actual isolated HA instance whose existing entries remain unchanged.
    @param provisioning Unix's fake target, journal and worker fixture.
    """
    entries = list(hass.config_entries.async_entries())
    flow = GuidedSetupPrototype(provisioning.call)
    state = review(flow)
    assert state["step"] == "review"
    assert state["plan"]["physical_install_supported"] is False
    assert len(state["plan"]["artifact_sha256"]) == 64
    assert state["plan"]["expires_at"] > provisioning.clock.now
    assert "request" not in flow.confirm_simulation()
    assert provisioning.store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    state = flow.confirm_simulation(True)
    request_id = state["request"]["request_id"]
    assert state["step"] == "observing" and provisioning.transfers == 0
    provisioning.transfer(request_id)
    state = flow.refresh()
    assert state["job"]["transfer_percent"] == 100
    assert state["step"] == "observing"
    assert state["job"]["application_verified"] is False
    provisioning.finish(request_id)
    state = flow.refresh()
    assert state["step"] == "firmware_verified_setup_required"
    assert state["job"]["network_ready"] is False
    assert state["job"]["setup_required"] is True
    assert list(hass.config_entries.async_entries()) == entries


def test_lost_submission_reply_restores_observer_without_resubmission(provisioning):
    """A lost reply and reconstructed UI retain one request and only observe its job.

    @param provisioning Direct-call fake service and journal.
    """
    calls = []

    def transport(action, payload=None):
        """Lose one accepted start reply without changing the model's durable result.

        @param action Fake service action to record.
        @param payload Exact prototype request body.
        """
        calls.append(action)
        response = provisioning.call(action, payload)
        if action == "start":
            raise TimeoutError("lost response")
        return response

    flow = GuidedSetupPrototype(transport)
    review(flow)
    with pytest.raises(TimeoutError):
        flow.confirm_simulation(True)
    saved = json.loads(json.dumps(flow.state))
    restored = GuidedSetupPrototype(transport, saved)
    assert restored.begin()["step"] == "observing"
    assert restored.confirm_simulation(True)["request"] == saved["request"]
    assert calls.count("start") == 1
    assert provisioning.transfers == 0


def test_service_restart_shows_recovery_without_new_work(provisioning):
    """Restart after possible transfer fences the job and leaves the observer read-only.

    @param provisioning Fake worker with durable crash recovery semantics.
    """
    flow = GuidedSetupPrototype(provisioning.call)
    review(flow)
    state = flow.confirm_simulation(True)
    provisioning.transfer(state["request"]["request_id"])
    provisioning.restart()
    state = flow.refresh()
    assert state["step"] == "inspect_recovery"
    assert state["job"]["status"] == "unknown"
    assert state["job"]["recovery"] == "recovery_required"
    assert flow.confirm_simulation(True)["job"] == state["job"]
    assert provisioning.transfers == 1


@pytest.mark.parametrize("change", ["expiry", "target", "ownership"])
def test_review_does_not_authorize_changed_or_expired_plan(provisioning, change):
    """Explicit confirmation still obeys the service's fresh target and plan checks.

    @param provisioning Fake service with server-controlled evidence.
    @param change Evidence invalidation after the user reviews a plan.
    """
    flow = GuidedSetupPrototype(provisioning.call)
    review(flow)
    if change == "expiry":
        provisioning.clock.now += 121
    elif change == "target":
        provisioning.replace_target(generation="replacement-2")
    else:
        provisioning.replace_target(ownership="conflict")
    with pytest.raises(
        ValueError, match="plan_expired|plan_changed|ownership_unresolved"
    ):
        flow.confirm_simulation(True)
    assert provisioning.store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert provisioning.transfers == 0


@pytest.mark.parametrize("change", ["instance", "target", "artifact"])
def test_observer_rejects_changed_identity(provisioning, change):
    """An observer cannot attach the selected job to a replacement service or device.

    @param provisioning Original fake service with one admitted job.
    @param change Response identity field substituted by the test transport.
    """
    flow = GuidedSetupPrototype(provisioning.call)
    review(flow)
    flow.confirm_simulation(True)

    def substitute(action, payload=None):
        """Alter one observed response without mutating the original journal.

        @param action Read action requested by the observer.
        @param payload Existing job identifier.
        """
        response = deepcopy(provisioning.call(action, payload))
        if change == "instance":
            response["instance_id"] = "replacement-instance"
        else:
            key = "target_id" if change == "target" else "artifact_sha256"
            response["data"][key] = "replacement"
        return response

    flow.transport = substitute
    with pytest.raises(ValueError, match="instance_changed|job_changed"):
        flow.refresh()
    assert provisioning.transfers == 0


@pytest.mark.parametrize(
    "field,value", [("simulated", False), ("physical_install_supported", True)]
)
def test_prototype_never_accepts_real_install_capability(provisioning, field, value):
    """This test-only UI cannot become a physical installer through advertised capabilities.

    @param provisioning Fake service used only to supply an initial response.
    @param field Envelope or capability flag changed by the test.
    @param value Nonprototype capability value.
    """
    calls = []

    def changed_capability(action, payload=None):
        """Change one capability before the prototype can select or submit work.

        @param action Initial info read only.
        @param payload Empty request body.
        """
        calls.append(action)
        response = deepcopy(provisioning.call(action, payload))
        target = response if field == "simulated" else response["data"]
        target[field] = value
        return response

    with pytest.raises(ValueError, match="prototype_"):
        GuidedSetupPrototype(changed_capability).begin()
    assert calls == ["info"]
    assert provisioning.transfers == 0
