"""Native HA setup dialogs retain physical intent, identities and explicit consent."""

from copy import deepcopy
import asyncio
import json
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livolo_ec._client import LivoloError
from custom_components.livolo_ec._client.bootstrap import BootstrapError
from custom_components.livolo_ec.bootstrap_flow import BootstrapFlowMixin
from custom_components.livolo_ec.bootstrap_observer import observer_for


def assert_radio_placeholders(result):
    """Match real form values to both new-hub/options templates and their English translation.

    @param result Actual radio form result with its step and description placeholders.
    """
    root = Path(__file__).resolve().parents[2] / "custom_components/livolo_ec"
    expected = set(result["description_placeholders"])
    for filename in ("strings.json", "translations/en.json"):
        content = json.loads((root / filename).read_text())
        for section in ("config", "options"):
            description = content[section]["step"][result["step_id"]]["description"]
            actual = {
                field for _, field, _, _ in Formatter().parse(description) if field
            }
            assert actual == expected, (filename, section, result["step_id"])


class PhysicalContractFixture:
    """Model HTTP-client responses only; real-controller loopback tests are separate."""

    def __init__(self, network="2a992b12:a7", mode="gateway", profile="zb-gw04-1v2"):
        """Create deterministic metadata with no device or serial dependency.

        @param network Existing network identity, or None for initial setup.
        @param mode Initial service mode.
        @param profile Exact board advertised consistently across the setup contract.
        """
        self.meta = dict(
            instance_id="physical-instance",
            mode=mode,
            physical_install_supported=True,
            prepare_supported=True,
            enter_supported=mode == "gateway",
            initialize_supported=network is None,
            profile=profile,
            expected_network_id=network,
            active_request_id=None,
            latest_request_id=None,
            blocked_reason=None,
            cancel_supported=mode == "bootstrap",
            baseline_available=network is not None,
        )
        self.target = dict(
            target_id="radio-1",
            board=profile,
            label="Test radio",
            candidate_id="image-1",
            version="0.14",
            artifact_sha256="a" * 64,
            requires_bootloader=True,
        )
        self.plan = dict(
            plan_id="plan-1",
            instance_id="physical-instance",
            target_id="radio-1",
            candidate_id="image-1",
            profile=profile,
            version="0.14",
            artifact_sha256="a" * 64,
            expires_at=2_000_000_000,
            requires_confirmation=True,
        )
        self.calls = []
        self.results = {}
        self.lose = None
        self.pending = None
        self.reject = None

    async def info(self, instance_id=None):
        """Read static physical service metadata.

        @param instance_id Expected identity, checked by real client in transport tests.
        """
        return deepcopy(self.meta)

    async def targets(self, instance_id):
        """List one server-owned exact board/image choice without I/O.

        @param instance_id Expected service identity.
        """
        return dict(targets=[deepcopy(self.target)], blocked_reason=None)

    async def operation(self, request_id, instance_id):
        """Read the same durable operation after response loss.

        @param request_id Previously submitted request identity.
        @param instance_id Expected service identity.
        """
        if request_id not in self.results:
            raise BootstrapError("unknown_operation")
        return deepcopy(self.results[request_id])

    async def submit(self, operation, body, instance_id):
        """Simulate durable acceptance before optionally losing its reply.

        @param operation Explicit lifecycle action selected in HA.
        @param body Persisted physical request body.
        @param instance_id Expected service identity.
        """
        self.calls.append((operation, deepcopy(body)))
        result = dict(
            request_id=body["request_id"],
            instance_id=instance_id,
            kind=operation,
            status="confirmed",
            stage=operation + "_confirmed",
            error=None,
            recovery=None,
            application_verified=False,
            network_ready=False,
            setup_required=True,
            plan=None,
            transfer_percent=None,
            expected_network_id=self.meta["expected_network_id"],
        )
        if operation == "enter":
            self.meta.update(
                mode="bootstrap", enter_supported=False, cancel_supported=True
            )
        elif operation == "prepare":
            self.plan["plan_id"] = (
                f"plan-{sum(kind == 'prepare' for kind, _ in self.calls)}"
            )
            result.update(plan=deepcopy(self.plan))
        elif operation == "install":
            result.update(
                {
                    key: self.plan[key]
                    for key in (
                        "plan_id",
                        "target_id",
                        "candidate_id",
                        "artifact_sha256",
                        "profile",
                        "version",
                    )
                }
            )
            result.update(application_verified=True, stage="application_verified")
            self.meta["cancel_supported"] = False
        elif operation == "initialize":
            self.meta.update(
                expected_network_id="2a992b12:a7", initialize_supported=False
            )
            result.update(network={"gateway": "2a992b12", "tag": "a7"})
        elif operation == "handoff":
            result.update(
                install_request_id=body["install_request_id"], network_ready=True
            )
            self.meta.update(mode="gateway", enter_supported=True)
        elif operation == "cancel":
            if self.meta["baseline_available"]:
                result.update(network_ready=True)
                self.meta.update(mode="gateway", enter_supported=True)
            else:
                result.update(cancelled_unconfigured=True)
            self.meta["cancel_supported"] = False
        if operation == self.reject:
            result.update(
                status="rejected",
                application_verified=False,
                network_ready=False,
                error="plan_expired",
            )
            self.meta["cancel_supported"] = True
            if operation == "cancel":
                self.meta.update(mode="bootstrap", enter_supported=False)
                result["error"] = "bootloader_not_ready"
        if operation == self.pending:
            result.update(
                status="pending", application_verified=False, network_ready=False
            )
        self.results[body["request_id"]] = deepcopy(result)
        self.meta["latest_request_id"] = body["request_id"]
        if operation == self.lose:
            raise LivoloError("Response lost after acceptance")
        return result


@pytest.fixture
def setup_gateway(hass, envelope):
    """Provide an existing HA entry and deterministic nonphysical client responses.

    @param hass Actual HA test instance.
    @param envelope Ready normal-network snapshot.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "secret"},
        options={"development": False, "preserved": "option"},
    )
    entry.add_to_hass(hass)
    physical = PhysicalContractFixture()
    native = SimpleNamespace(
        info=AsyncMock(
            return_value={
                "api_version": 1,
                "gateway": "2a992b12",
                "tag": "a7",
                "simulated": False,
            }
        ),
        snapshot=AsyncMock(return_value=envelope),
    )
    with (
        patch(
            "custom_components.livolo_ec.bootstrap_flow.BootstrapClient",
            return_value=physical,
        ),
        patch("custom_components.livolo_ec.bootstrap_flow.Client", return_value=native),
    ):
        yield SimpleNamespace(entry=entry, physical=physical, native=native)


async def open_setup(hass, entry):
    """Open an existing hub's native radio setup without submitting a physical action.

    @param hass Actual HA instance.
    @param entry Existing network config entry.
    """
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "radio_setup"}
    )


async def to_review(hass, setup):
    """Explicitly enter setup and inspect the acknowledged target, stopping before flash.

    @param hass Actual HA instance.
    @param setup Entry/client fixture.
    """
    result = await open_setup(hass, setup.entry)
    assert result["step_id"] == "radio_enter"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    assert result["step_id"] == "radio_select"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "target_id": "radio-1",
            "board_confirmed": True,
            "dedicated_radio": True,
            "bootloader_ready": True,
        },
    )
    assert result["step_id"] == "radio_review"
    return result


async def seed_unknown_install(hass, setup):
    """Persist a consumed plan and unknown Install exactly as a restored HA observer sees them.

    @param hass Actual HA test instance and storage.
    @param setup Existing hub/client fixture.
    """
    physical = setup.physical
    physical.meta.update(mode="bootstrap", setup_session="session-2")
    physical.meta["profile"] = "sonoff-zbdongle-e"
    physical.target["board"] = "sonoff-zbdongle-e"
    physical.plan["profile"] = "sonoff-zbdongle-e"
    physical.plan["plan_id"] = "consumed-plan"
    body = dict(
        request_id="unknown-install",
        instance_id="physical-instance",
        plan_id="consumed-plan",
        confirmed=True,
    )
    receipt = await physical.submit("install", body, "physical-instance")
    receipt.update(
        status="unknown",
        stage="verifying_target",
        recovery="recovery_required",
        error="inspection_timeout",
        application_verified=False,
    )
    physical.results[body["request_id"]] = deepcopy(receipt)
    record = dict(
        reconciliation_id="reconciliation-1",
        request_id=body["request_id"],
        kind="install",
        plan_id="consumed-plan",
        target_id="radio-1",
        resolution="verified_no_flash",
        permits="prepare_only",
        setup_session="session-2",
    )
    physical.meta["reconciliation"] = record
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    await observer.bind("physical-instance", setup.entry.unique_id)
    await observer.save(
        pending={"operation": "install", "body": body},
        plan=physical.plan,
        install=receipt,
        last_operation=receipt,
        selection=physical.target,
        completed_requests={"prepare": {"request_id": "old-prepare"}},
    )
    hass.data.pop("livolo_ec_bootstrap_observers")
    return deepcopy(receipt), deepcopy(record)


@pytest.mark.parametrize("remote", [False, True])
async def test_reconciliation_restores_audit_and_requires_separate_fresh_consent(
    hass, setup_gateway, remote
):
    """Reopening only acknowledges authority; fresh Prepare and Install remain separate explicit actions.

    @param hass Actual HA storage and options lifecycle.
    @param setup_gateway Existing hub with deterministic daemon responses.
    @param remote Whether the current target offers explicitly confirmed remote entry.
    """
    setup = setup_gateway
    receipt, record = await seed_unknown_install(hass, setup)
    physical = setup.physical
    if remote:
        physical.target["entry_methods"] = ["manual", "sonoff_sniffer_0_12"]
    result = await open_setup(hass, setup.entry)
    assert result["step_id"] == ("radio_detect" if remote else "radio_select")
    assert_radio_placeholders(result)
    assert (
        "remains unknown" in result["description_placeholders"]["reconciliation_notice"]
    )
    assert "cancel_setup" not in result["data_schema"].schema
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    assert observer.state["pending"] is None
    assert observer.state["plan"] is None and observer.state["install"] is None
    audit = deepcopy(observer.state["reconciliations"])
    assert audit[record["reconciliation_id"]]["operation"] == receipt
    assert audit[record["reconciliation_id"]]["plan"]["plan_id"] == "consumed-plan"
    assert observer.state["last_operation"] == receipt
    flow = hass.config_entries.options._progress[result["flow_id"]]
    for method in (flow.async_step_radio_initialize, flow.async_step_radio_handoff):
        denied = await method({"confirmed": True})
        assert denied["errors"]
    denied = await flow.async_step_radio_review(
        {"confirmed": True, "dedicated_radio": True}
    )
    assert denied["errors"]
    assert len(physical.calls) == 1
    hass.config_entries.options.async_abort(result["flow_id"])
    hass.data.pop("livolo_ec_bootstrap_observers")
    result = await open_setup(hass, setup.entry)
    assert result["step_id"] == ("radio_detect" if remote else "radio_select")
    assert len(physical.calls) == 1
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "target_id": "radio-1",
            "board_confirmed": True,
            "dedicated_radio": True,
            "entry_confirmed" if remote else "bootloader_ready": True,
        },
    )
    assert result["step_id"] == "radio_review"
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    assert observer.state["reconciliation_ack"]["awaiting_prepare"] is False
    assert observer.state["plan"]["plan_id"] != "consumed-plan"
    assert physical.calls[-1][0] == "prepare"
    assert physical.calls[-1][1]["request_id"] != receipt["request_id"]
    assert observer.state["reconciliations"] == audit
    assert physical.results[receipt["request_id"]] == receipt
    assert [kind for kind, _ in physical.calls] == ["install", "prepare"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    assert result["step_id"] == "radio_handoff"
    assert [kind for kind, _ in physical.calls] == ["install", "prepare", "install"]


@pytest.mark.parametrize(
    "mismatch",
    [
        "absent",
        "active",
        "prepare_unsupported",
        "request",
        "plan",
        "target",
        "result_status",
        "result_recovery",
        "result_stage",
        "result_error",
        "result_profile",
        "result_instance",
        "result_network",
        "transferred",
        "verified",
        "missing_target",
        "changed_session",
        "changed_record",
        "new_active",
        "instance",
        "network",
    ],
)
async def test_reconciliation_does_not_release_unmatched_unknown_intent(
    hass, setup_gateway, mismatch
):
    """Incompatible receipts or changing authority preserve the pending Install without any POST.

    @param hass Actual HA storage and flow lifecycle.
    @param setup_gateway Existing hub and fake physical service.
    @param mismatch Missing binding or race introduced between authority reads.
    """
    setup = setup_gateway
    receipt, _ = await seed_unknown_install(hass, setup)
    physical = setup.physical
    record = physical.meta["reconciliation"]
    if mismatch == "absent":
        physical.meta.pop("reconciliation")
    elif mismatch == "active":
        physical.meta["active_request_id"] = "other-active"
    elif mismatch == "prepare_unsupported":
        physical.meta["prepare_supported"] = False
    elif mismatch in ("request", "plan", "target"):
        record[mismatch + "_id"] = "unmatched"
    elif mismatch == "missing_target":
        physical.target["target_id"] = "different-radio"
    elif mismatch in ("instance", "network"):
        physical.meta[
            "instance_id" if mismatch == "instance" else "expected_network_id"
        ] = "replacement"
    elif mismatch.startswith("result_") or mismatch in ("transferred", "verified"):
        key, value = {
            "result_status": ("status", "confirmed"),
            "result_recovery": ("recovery", None),
            "result_stage": ("stage", "transferring"),
            "result_error": ("error", "transfer_failed"),
            "result_profile": ("profile", "zb-gw04-1v2"),
            "result_instance": ("instance_id", "replacement"),
            "result_network": ("expected_network_id", None),
            "transferred": ("transfer_percent", 0),
            "verified": ("application_verified", True),
        }[mismatch]
        physical.results[receipt["request_id"]][key] = value
    else:
        second = deepcopy(physical.meta)
        if mismatch == "changed_session":
            second["setup_session"] = "session-3"
        elif mismatch == "changed_record":
            second["reconciliation"]["reconciliation_id"] = "replacement"
        else:
            second["active_request_id"] = "new-active"
        physical.info = AsyncMock(side_effect=[deepcopy(physical.meta), second])
    result = await open_setup(hass, setup.entry)
    assert result["step_id"] in ("radio_progress", "radio_setup")
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    assert observer.state["pending"]["body"]["request_id"] == receipt["request_id"]
    assert observer.state["plan"]["plan_id"] == "consumed-plan"
    assert not observer.state.get("reconciliation_ack")
    assert not observer.state.get("reconciliations")
    assert len(physical.calls) == 1


@pytest.mark.parametrize("outcome", ["pending", "lost_reply", "consumed_plan"])
@pytest.mark.parametrize("rotated_session", [False, True])
async def test_reconciliation_never_replays_or_clears_new_preparation(
    hass, setup_gateway, outcome, rotated_session
):
    """A retained acknowledgment cannot clear newer intent or accept the consumed old plan.

    @param hass Actual HA persistence and options lifecycle.
    @param setup_gateway Bound service fixture.
    @param outcome Fresh preparation remains pending, loses its reply or returns the consumed plan.
    @param rotated_session Whether remote entry hides the old record by rotating the daemon session.
    """
    setup = setup_gateway
    await seed_unknown_install(hass, setup)
    physical = setup.physical
    result = await open_setup(hass, setup.entry)
    if outcome == "pending":
        physical.pending = "prepare"
    elif outcome == "lost_reply":
        physical.lose = "prepare"
    else:
        submit = physical.submit

        async def consumed(operation, body, instance_id):
            """Return the consumed plan after recording one explicit preparation.

            @param operation Requested preparation.
            @param body Persisted new consent request.
            @param instance_id Bound service instance.
            """
            response = await submit(operation, body, instance_id)
            response["plan"]["plan_id"] = "consumed-plan"
            physical.results[body["request_id"]] = deepcopy(response)
            return response

        physical.submit = consumed
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        dict(
            target_id="radio-1",
            board_confirmed=True,
            dedicated_radio=True,
            bootloader_ready=True,
        ),
    )
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    pending = deepcopy(observer.state["pending"])
    assert pending["operation"] == "prepare"
    assert observer.state["reconciliation_ack"]["awaiting_prepare"] is True
    # Remote entry may rotate the daemon session before its reply is observed.
    if rotated_session:
        physical.meta.update(setup_session="session-3", reconciliation=None)
    hass.config_entries.options.async_abort(result["flow_id"])
    hass.data.pop("livolo_ec_bootstrap_observers")
    result = await open_setup(hass, setup.entry)
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    if outcome == "lost_reply":
        assert result["step_id"] == "radio_review"
        assert observer.state["pending"] is None
        assert observer.state["reconciliation_ack"]["awaiting_prepare"] is False
    else:
        assert observer.state["pending"] == pending
        assert observer.state["reconciliation_ack"]["awaiting_prepare"] is True
    assert [kind for kind, _ in physical.calls] == ["install", "prepare"]


async def test_reconciliation_new_hub_keeps_unknown_history_without_creating_hub(
    hass, setup_gateway
):
    """A new-hub flow consumes the same authority without claiming readiness or querying a gateway.

    @param hass Actual HA config lifecycle and persistent storage.
    @param setup_gateway Physical response fixture borrowed without its existing network binding.
    """
    setup = setup_gateway
    receipt, record = await seed_unknown_install(hass, setup)
    physical = setup.physical
    physical.meta["expected_network_id"] = None
    physical.meta["baseline_available"] = False
    physical.meta["initialize_supported"] = False
    physical.target["entry_methods"] = ["manual", "sonoff_sniffer_0_12"]
    receipt["expected_network_id"] = None
    physical.results[receipt["request_id"]] = deepcopy(receipt)
    url = "http://new-reconciliation.invalid:8090"
    observer = observer_for(hass, url)
    await observer.bind("physical-instance", None)
    await observer.save(plan=physical.plan, install=receipt, last_operation=receipt)
    hass.data.pop("livolo_ec_bootstrap_observers")
    result = await hass.config_entries.flow.async_init(
        "livolo_ec",
        context={"source": "user"},
        data={"url": url, "token": "secret", "setup_radio": True},
    )
    assert result["step_id"] == "radio_detect"
    assert_radio_placeholders(result)
    restored = observer_for(hass, url)
    assert (
        restored.state["reconciliations"][record["reconciliation_id"]]["operation"]
        == receipt
    )
    assert restored.state["reconciliation_ack"]["awaiting_prepare"] is True
    assert len(hass.config_entries.async_entries("livolo_ec")) == 1
    setup.native.info.assert_not_awaited()
    setup.native.snapshot.assert_not_awaited()
    assert len(physical.calls) == 1


async def test_acknowledged_reconciliation_cannot_change_meaning(hass, setup_gateway):
    """A reused reconciliation identity cannot overwrite audit history or unlock another target.

    @param hass Actual HA options lifecycle and storage.
    @param setup_gateway Physical service fixture.
    """
    setup = setup_gateway
    await seed_unknown_install(hass, setup)
    result = await open_setup(hass, setup.entry)
    observer = observer_for(hass, setup.entry.data["url"], setup.entry.entry_id)
    audit = deepcopy(observer.state["reconciliations"])
    setup.physical.meta["reconciliation"]["target_id"] = "replacement-radio"
    hass.config_entries.options.async_abort(result["flow_id"])
    result = await open_setup(hass, setup.entry)
    assert result["errors"] == {"base": "bootstrap_incompatible"}
    assert observer.state["reconciliations"] == audit
    assert observer.state["reconciliation_ack"]["awaiting_prepare"] is True
    assert len(setup.physical.calls) == 1


async def test_explicit_guided_handoff_preserves_existing_entry(hass, setup_gateway):
    """Only explicit actions inspect, install and hand off; existing entry data stays intact.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub and nonphysical responses.
    """
    setup = setup_gateway
    original = dict(setup.entry.data)
    result = await to_review(hass, setup)
    assert [kind for kind, _ in setup.physical.calls] == ["enter", "prepare"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": False, "dedicated_radio": True}
    )
    assert result["step_id"] == "radio_review"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    assert result["step_id"] == "radio_handoff"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {"development": False, "preserved": "option"}
    assert setup.entry.data == original and setup.entry.unique_id == "2a992b12:a7"
    assert [kind for kind, _ in setup.physical.calls] == [
        "enter",
        "prepare",
        "install",
        "handoff",
    ]


@pytest.mark.parametrize("lost", ["prepare", "install", "handoff"])
async def test_lost_reply_resumes_same_request_without_post_replay(
    hass, setup_gateway, lost
):
    """Closing and reopening a flow observes the accepted request instead of submitting again.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub and durable response fixture.
    @param lost Operation whose accepted reply disappears.
    """
    setup = setup_gateway
    if lost == "prepare":
        setup.physical.lose = lost
        result = await open_setup(hass, setup.entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirmed": True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"refresh": True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "target_id": "radio-1",
                "board_confirmed": True,
                "dedicated_radio": True,
                "bootloader_ready": True,
            },
        )
    else:
        result = await to_review(hass, setup)
        setup.physical.lose = lost
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirmed": True, "dedicated_radio": True}
        )
        if lost == "handoff":
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"confirmed": True}
            )
    assert result["step_id"] == "radio_setup"
    before = deepcopy(setup.physical.calls)
    hass.config_entries.options.async_abort(result["flow_id"])
    resumed = await open_setup(hass, setup.entry)
    assert resumed["type"] in ("form", "create_entry")
    assert setup.physical.calls == before
    assert [kind for kind, _ in before].count(lost) == 1


async def test_duplicate_completed_install_form_observes_original_job(
    hass, setup_gateway
):
    """A stale repeated confirmation cannot mint a new install request for a consumed plan.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub and confirmed operation fixture.
    """
    result = await to_review(hass, setup_gateway)
    flow = hass.config_entries.options._progress[result["flow_id"]]
    submitted = {"confirmed": True, "dedicated_radio": True}
    await flow.async_step_radio_review(submitted)
    await flow.async_step_radio_review(submitted)
    assert [kind for kind, _ in setup_gateway.physical.calls].count("install") == 1


async def test_unknown_after_lost_post_never_clears_intent(hass, setup_gateway):
    """Missing durable lookup after an ambiguous POST cannot authorize a replacement request.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub and response-loss fixture.
    """
    result = await to_review(hass, setup_gateway)
    setup_gateway.physical.lose = "install"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    setup_gateway.physical.results.clear()
    before = deepcopy(setup_gateway.physical.calls)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    assert result["errors"] == {"base": "bootstrap_unavailable"}
    assert setup_gateway.physical.calls == before


async def test_handoff_cannot_replace_network_identity(hass, setup_gateway):
    """Even a claimed completed handoff cannot override independent normal-network checks.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub and fake physical response fixture.
    """
    result = await to_review(hass, setup_gateway)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    setup_gateway.native.info.return_value["gateway"] = "ffffffff"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    assert result["errors"] == {"base": "bootstrap_incompatible"}
    assert setup_gateway.entry.unique_id == "2a992b12:a7"


@pytest.mark.parametrize("lost", [False, True])
async def test_declining_install_can_resume_existing_hub(hass, setup_gateway, lost):
    """Cancel before a flash explicitly restores a verified existing network, even after reply loss.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub with preserved baseline.
    @param lost Whether the cancel reply is lost after durable completion.
    """
    result = await to_review(hass, setup_gateway)
    if lost:
        setup_gateway.physical.lose = "cancel"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"cancel_setup": True, "confirmed": False, "dedicated_radio": False},
    )
    assert result["step_id"] == "radio_cancel"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    if lost:
        assert result["step_id"] == "radio_setup"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"refresh": True}
        )
    assert result["type"] == "create_entry"
    assert [kind for kind, _ in setup_gateway.physical.calls] == [
        "enter",
        "prepare",
        "cancel",
    ]


async def test_durable_rejection_allows_explicit_new_review(hass, setup_gateway):
    """A proved admission rejection permits fresh inspection only after an explicit review-again action.

    @param hass Actual HA instance.
    @param setup_gateway Deterministic physical contract fixture.
    """
    result = await to_review(hass, setup_gateway)
    setup_gateway.physical.reject = "install"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    assert result["step_id"] == "radio_progress"
    count = len(setup_gateway.physical.calls)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"review_again": True}
    )
    assert result["step_id"] == "radio_select"
    assert len(setup_gateway.physical.calls) == count


async def test_known_zha_port_conflict_blocks_inspection(hass, setup_gateway):
    """A reliably matching enabled ZHA port cannot be inspected or silently stopped.

    @param hass Actual HA instance.
    @param setup_gateway Exact target with a server-owned port alias.
    """
    setup_gateway.physical.target["port_aliases"] = ["/dev/serial/by-id/test-radio"]
    conflict = MockConfigEntry(
        domain="zha", data={"device": {"path": "/dev/serial/by-id/test-radio"}}
    )
    conflict.add_to_hass(hass)
    result = await open_setup(hass, setup_gateway.entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "target_id": "radio-1",
            "board_confirmed": True,
            "dedicated_radio": True,
            "bootloader_ready": True,
        },
    )
    assert result["errors"] == {"base": "bootstrap_unavailable"}
    assert "known\\_zha\\_conflict" in result["description_placeholders"]["reason"]
    assert [kind for kind, _ in setup_gateway.physical.calls] == ["enter"]
    assert conflict.disabled_by is None


@pytest.mark.parametrize("profile", ["zb-gw04-1v2", "sonoff-zbdongle-e"])
async def test_new_hub_requires_initialization_handoff_and_real_readiness(
    hass, envelope, profile
):
    """A bootstrap instance cannot become a hub until explicit initialization and normal ready agreement.

    @param hass Actual HA instance.
    @param envelope Normal-network snapshot supplied only after handoff.
    @param profile Exact board whose independent instructions precede inspection.
    """
    physical = PhysicalContractFixture(network=None, mode="bootstrap", profile=profile)
    native = SimpleNamespace(
        info=AsyncMock(
            return_value={
                "api_version": 1,
                "gateway": "2a992b12",
                "tag": "a7",
                "simulated": False,
            }
        ),
        snapshot=AsyncMock(return_value=envelope),
    )
    with (
        patch(
            "custom_components.livolo_ec.bootstrap_flow.BootstrapClient",
            return_value=physical,
        ),
        patch("custom_components.livolo_ec.bootstrap_flow.Client", return_value=native),
        patch("custom_components.livolo_ec.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            "livolo_ec",
            context={"source": "user"},
            data={
                "url": "http://new.invalid:8090",
                "token": "secret",
                "setup_radio": True,
            },
        )
        assert result["step_id"] == "radio_select"
        assert not physical.meta["baseline_available"]
        assert_radio_placeholders(result)
        native.info.assert_not_awaited()
        native.snapshot.assert_not_awaited()
        instructions = result["description_placeholders"]["instructions"]
        if profile == "sonoff-zbdongle-e":
            assert "release both buttons together" in instructions
            assert "three seconds" not in instructions
            assert result["description_placeholders"]["board"] == "Sonoff ZBDongle-E"
        else:
            assert "three seconds" in instructions
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "target_id": "radio-1",
                "board_confirmed": True,
                "dedicated_radio": True,
                "bootloader_ready": True,
            },
        )
        assert (
            "No previous Livolo gateway"
            in result["description_placeholders"]["network_effect"]
        )
        assert_radio_placeholders(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirmed": True, "dedicated_radio": True}
        )
        assert result["step_id"] == "radio_initialize"
        assert hass.config_entries.async_entries("livolo_ec") == []
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirmed": True}
        )
        assert result["step_id"] == "radio_handoff"
        assert hass.config_entries.async_entries("livolo_ec") == []
        native.info.assert_not_awaited()
        native.snapshot.assert_not_awaited()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirmed": True}
        )
        assert result["type"] == "create_entry"
        assert result["result"].unique_id == "2a992b12:a7"
        assert result["data"] == {"url": "http://new.invalid:8090", "token": "secret"}
        assert [kind for kind, _ in physical.calls] == [
            "prepare",
            "install",
            "initialize",
            "handoff",
        ]


async def test_expired_plan_explicit_reinspection_gets_new_request(hass, setup_gateway):
    """A new displayed inspection confirmation supersedes an expired plan without automatic retry.

    @param hass Actual HA instance.
    @param setup_gateway Client fixture advertising an expired first plan.
    """
    setup_gateway.physical.plan["expires_at"] = 1
    result = await to_review(hass, setup_gateway)
    first = [body for kind, body in setup_gateway.physical.calls if kind == "prepare"][
        0
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"confirmed": False, "dedicated_radio": False, "inspect_again": True},
    )
    assert result["step_id"] == "radio_select"
    assert (
        len([kind for kind, _ in setup_gateway.physical.calls if kind == "prepare"])
        == 1
    )
    setup_gateway.physical.plan["expires_at"] = 2_000_000_000
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "target_id": "radio-1",
            "board_confirmed": True,
            "dedicated_radio": True,
            "bootloader_ready": True,
        },
    )
    assert result["step_id"] == "radio_review"
    prepared = [
        body for kind, body in setup_gateway.physical.calls if kind == "prepare"
    ]
    assert len(prepared) == 2 and prepared[-1]["request_id"] != first["request_id"]
    assert setup_gateway.physical.plan["plan_id"] == "plan-2"


async def test_rejected_inspection_can_be_explicitly_retried(hass, setup_gateway):
    """Corrected physical setup can be inspected again after a durable rejection and renewed review.

    @param hass Actual HA instance.
    @param setup_gateway Response fixture rejecting the first inspection.
    """
    setup_gateway.physical.reject = "prepare"
    result = await open_setup(hass, setup_gateway.entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    confirmed = {
        "target_id": "radio-1",
        "board_confirmed": True,
        "dedicated_radio": True,
        "bootloader_ready": True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], confirmed
    )
    assert result["step_id"] == "radio_progress"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"review_again": True}
    )
    setup_gateway.physical.reject = None
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], confirmed
    )
    assert result["step_id"] == "radio_review"
    assert (
        len([kind for kind, _ in setup_gateway.physical.calls if kind == "prepare"])
        == 2
    )


async def test_duplicate_inspection_confirmation_is_one_post(hass, setup_gateway):
    """Concurrent duplicate submissions of one displayed inspection use one durable request.

    @param hass Actual HA instance.
    @param setup_gateway Client fixture with one selected radio.
    """
    result = await open_setup(hass, setup_gateway.entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    flow = hass.config_entries.options._progress[result["flow_id"]]
    confirmed = {
        "target_id": "radio-1",
        "board_confirmed": True,
        "dedicated_radio": True,
        "bootloader_ready": True,
    }
    results = await asyncio.gather(
        flow.async_step_radio_select(confirmed), flow.async_step_radio_select(confirmed)
    )
    assert all(result["step_id"] == "radio_review" for result in results)
    assert (
        len([kind for kind, _ in setup_gateway.physical.calls if kind == "prepare"])
        == 1
    )


@pytest.mark.parametrize(
    "status,stage,binding,allowed",
    [
        ("rejected", "rejected", None, True),
        ("rejected", "rejected", "different-plan", False),
        ("unknown", "interrupted", None, False),
        ("pending", "accepted", None, False),
    ],
)
async def test_install_admission_refusal_keeps_binding_checks(
    hass, setup_gateway, status, stage, binding, allowed
):
    """Only definitive admission refusal can omit image facts; conflicting or uncertain results fail closed.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub client fixture.
    @param status Authoritative result status.
    @param stage Reported admission or execution stage.
    @param binding Optional returned plan identity.
    @param allowed Whether this is a clean refusal without contradictory metadata.
    """
    result = await to_review(hass, setup_gateway)
    flow = hass.config_entries.options._progress[result["flow_id"]]
    _, _, observer = flow._radio_clients()
    body = await observer.intent(
        "install",
        {"instance_id": "physical-instance", "plan_id": "plan-1", "confirmed": True},
    )
    refused = {
        "request_id": body["request_id"],
        "kind": "install",
        "status": status,
        "stage": stage,
        "error": "plan_unknown",
        "recovery": None,
        "plan_id": binding,
        "application_verified": False,
        "transfer_percent": None,
    }
    from custom_components.livolo_ec._client import IncompatibleError

    if allowed:
        await flow._radio_accept(observer, refused)
        assert observer.state["pending"] is None
        assert flow._radio_can_reinspect(refused)
    else:
        with pytest.raises(IncompatibleError):
            await flow._radio_accept(observer, refused)
        assert observer.state["pending"]["body"] == body


async def test_cancel_needs_no_prepare_or_catalog_and_explicit_retry_is_fresh(
    hass, setup_gateway
):
    """Cancellation checks the baseline directly; only renewed consent retries a definitive refusal.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub client fixture.
    """
    result = await open_setup(hass, setup_gateway.entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    flow = hass.config_entries.options._progress[result["flow_id"]]
    setup_gateway.physical.targets = AsyncMock(
        side_effect=AssertionError("Cancellation must not need an image catalog")
    )
    shown = await flow.async_step_radio_cancel()
    assert "BOOT" in shown["description_placeholders"]["instructions"]
    assert "three seconds" in shown["description_placeholders"]["instructions"]
    setup_gateway.physical.reject = "cancel"
    shown = await flow.async_step_radio_cancel({"confirmed": True})
    assert shown["step_id"] == "radio_progress"
    shown = await flow.async_step_radio_cancel({"confirmed": True})
    assert shown["step_id"] == "radio_progress"
    assert [kind for kind, _ in setup_gateway.physical.calls] == ["enter", "cancel"]
    shown = await flow.async_step_radio_progress({"cancel_setup": True})
    assert shown["step_id"] == "radio_cancel"
    setup_gateway.physical.reject = None
    shown = await flow.async_step_radio_cancel({"confirmed": True})
    assert shown["type"] == "create_entry"
    assert [kind for kind, _ in setup_gateway.physical.calls] == [
        "enter",
        "cancel",
        "cancel",
    ]
    first, second = [
        body for kind, body in setup_gateway.physical.calls if kind == "cancel"
    ]
    assert first["request_id"] != second["request_id"]
    setup_gateway.physical.targets.assert_not_awaited()


@pytest.mark.parametrize("availability", ["no_catalog", "no_prepare", "select"])
async def test_cancel_is_reachable_without_target_or_image(
    hass, setup_gateway, availability
):
    """The actual options form can cancel setup without a selected target or working image catalog.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub client fixture.
    @param availability Missing catalog, unavailable inspection, or an unselected target list.
    """
    physical = setup_gateway.physical
    physical.meta.update(mode="bootstrap", enter_supported=False, cancel_supported=True)
    if availability == "no_catalog":
        physical.targets = AsyncMock(
            return_value={"targets": [], "blocked_reason": "catalog_missing"}
        )
    elif availability == "no_prepare":
        physical.meta.update(prepare_supported=False, blocked_reason="catalog_missing")
    result = await open_setup(hass, setup_gateway.entry)
    assert result["step_id"] == (
        "radio_select" if availability == "select" else "radio_setup"
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"cancel_setup": True}
    )
    assert result["step_id"] == "radio_cancel"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": True}
    )
    assert result["type"] == "create_entry"
    assert [kind for kind, _ in physical.calls] == ["cancel"]


@pytest.mark.parametrize("source", ["meta", "target", "plan"])
async def test_engineering_image_warning_precedes_explicit_install(
    hass, setup_gateway, source
):
    """Engineering evidence remains visible on reviewed and reopened plans without authorizing installation.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub client fixture.
    @param source Metadata level carrying the engineering-only annotation.
    """
    getattr(setup_gateway.physical, source)["engineering_only"] = True
    result = await to_review(hass, setup_gateway)
    warning = "Engineering test only; not a qualified public firmware release"
    assert result["description_placeholders"]["qualification"] == warning
    hass.config_entries.options.async_abort(result["flow_id"])
    result = await open_setup(hass, setup_gateway.entry)
    assert result["step_id"] == "radio_review"
    assert result["description_placeholders"]["qualification"] == warning
    assert [kind for kind, _ in setup_gateway.physical.calls] == ["enter", "prepare"]


@pytest.mark.parametrize("network", [None, "2a992b12:a7"])
@pytest.mark.parametrize("restore_observer", [False, True])
async def test_sonoff_cancel_without_baseline_never_claims_restoration(
    hass, network, restore_observer
):
    """First setup cancellation creates no hub or gateway probe, even with configured network values.

    @param hass Actual HA instance.
    @param network Optional daemon configuration, which is not a verified gateway baseline.
    @param restore_observer Whether a fresh flow must restore its observer from HA storage.
    """
    physical = PhysicalContractFixture(
        network=network, mode="bootstrap", profile="sonoff-zbdongle-e"
    )
    physical.meta["baseline_available"] = False
    native = SimpleNamespace(
        info=AsyncMock(side_effect=AssertionError("No gateway before setup")),
        snapshot=AsyncMock(side_effect=AssertionError("No gateway before setup")),
    )
    with (
        patch(
            "custom_components.livolo_ec.bootstrap_flow.BootstrapClient",
            return_value=physical,
        ),
        patch("custom_components.livolo_ec.bootstrap_flow.Client", return_value=native),
    ):
        result = await hass.config_entries.flow.async_init(
            "livolo_ec",
            context={"source": "user"},
            data={
                "url": "http://new.invalid:8090",
                "token": "secret",
                "setup_radio": True,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"cancel_setup": True}
        )
        assert result["step_id"] == "radio_cancel"
        instructions = result["description_placeholders"]["instructions"]
        assert_radio_placeholders(result)
        assert "No previous Livolo gateway was verified" in instructions
        assert "does not restore" in instructions
        assert "No BOOT/RESET action" in instructions
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirmed": True}
        )
        assert result["type"] == "abort"
        assert result["reason"] == "bootstrap_cancelled"
        assert hass.config_entries.async_entries("livolo_ec") == []
        assert [kind for kind, _ in physical.calls] == ["cancel"]
        completed = deepcopy(physical.results)
        if restore_observer:
            hass.data.pop("livolo_ec_bootstrap_observers")
        reopened = await hass.config_entries.flow.async_init(
            "livolo_ec",
            context={"source": "user"},
            data={
                "url": "http://new.invalid:8090",
                "token": "secret",
                "setup_radio": True,
            },
        )
        assert reopened["type"] == "form"
        assert reopened["step_id"] == "radio_select"
        assert [kind for kind, _ in physical.calls] == ["cancel"]
        assert physical.results == completed
        assert hass.config_entries.async_entries("livolo_ec") == []
        native.info.assert_not_awaited()
        native.snapshot.assert_not_awaited()


@pytest.mark.parametrize("mismatch", ["unknown", "target", "changed", "plan"])
async def test_exact_board_instructions_cannot_authorize_other_profile(
    hass, setup_gateway, mismatch
):
    """Unknown, mixed or changed board identities never reach an installation review.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub with server-advertised physical setup evidence.
    @param mismatch Board substitution before selection, after display or in the prepared plan.
    """
    physical = setup_gateway.physical
    physical.meta.update(mode="bootstrap", enter_supported=False, cancel_supported=True)
    if mismatch == "unknown":
        physical.meta["profile"] = physical.target["board"] = "unreviewed-board"
    elif mismatch == "target":
        physical.target["board"] = "sonoff-zbdongle-e"
    result = await open_setup(hass, setup_gateway.entry)
    if mismatch in ("unknown", "target"):
        assert result["errors"] == {"base": "bootstrap_unavailable"}
        assert physical.calls == []
        return
    assert result["step_id"] == "radio_select"
    if mismatch == "changed":
        physical.meta["profile"] = "sonoff-zbdongle-e"
    else:
        physical.plan["profile"] = "sonoff-zbdongle-e"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "target_id": "radio-1",
            "board_confirmed": True,
            "dedicated_radio": True,
            "bootloader_ready": True,
        },
    )
    assert result["errors"] == {"base": "bootstrap_incompatible"}
    assert [kind for kind, _ in physical.calls] == (
        ["prepare"] if mismatch == "plan" else []
    )


async def test_sonoff_existing_gateway_cancel_uses_its_own_buttons(hass, setup_gateway):
    """Cancelling a real Sonoff gateway uses Sonoff instructions without ZB-GW04 timing.

    @param hass Actual HA instance.
    @param setup_gateway Existing gateway fixture with a verified baseline.
    """
    physical = setup_gateway.physical
    physical.meta.update(
        mode="bootstrap",
        enter_supported=False,
        cancel_supported=True,
        profile="sonoff-zbdongle-e",
    )
    physical.target["board"] = physical.plan["profile"] = "sonoff-zbdongle-e"
    result = await open_setup(hass, setup_gateway.entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"cancel_setup": True}
    )
    instructions = result["description_placeholders"]["instructions"]
    assert "release both buttons together" in instructions
    assert "three seconds" not in instructions
    assert "previous firmware before resuming" in instructions
    assert physical.calls == []


async def open_remote_setup(hass, setup):
    """Advertise only the exact Sonoff method and open its automatic form without a POST.

    @param hass Actual HA instance.
    @param setup Existing hub and physical-contract fixture.
    """
    physical = setup.physical
    physical.meta.update(
        mode="bootstrap",
        enter_supported=False,
        cancel_supported=True,
        profile="sonoff-zbdongle-e",
    )
    physical.target.update(
        board="sonoff-zbdongle-e", entry_methods=["manual", "sonoff_sniffer_0_12"]
    )
    physical.plan["profile"] = "sonoff-zbdongle-e"
    result = await open_setup(hass, setup.entry)
    assert result["step_id"] == "radio_detect"
    assert_radio_placeholders(result)
    assert physical.calls == []
    return result


@pytest.mark.parametrize("lost", [False, True])
async def test_remote_prepare_requires_distinct_consent_and_never_replays(
    hass, setup_gateway, lost
):
    """One advertised remote prepare needs new consent; a lost reply resumes only its saved request.

    @param hass Actual HA instance.
    @param setup_gateway Existing hub with controlled native responses.
    @param lost Whether the first preparation reply is lost after server completion.
    """
    result = await open_remote_setup(hass, setup_gateway)
    consent = dict(
        target_id="radio-1",
        board_confirmed=True,
        dedicated_radio=True,
        entry_confirmed=False,
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], consent
    )
    assert result["step_id"] == "radio_detect"
    assert setup_gateway.physical.calls == []
    if lost:
        setup_gateway.physical.lose = "prepare"
    consent["entry_confirmed"] = True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], consent
    )
    if lost:
        assert result["errors"] == {"base": "bootstrap_unavailable"}
        hass.config_entries.options.async_abort(result["flow_id"])
        result = await open_setup(hass, setup_gateway.entry)
    assert result["step_id"] == "radio_review"
    assert [kind for kind, _ in setup_gateway.physical.calls] == ["prepare"]
    submitted = setup_gateway.physical.calls[0][1]
    assert submitted["entry_method"] == "sonoff_sniffer_0_12"
    assert submitted["entry_confirmed"] is True
    assert "bootloader_ready" not in submitted
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirmed": False, "dedicated_radio": True}
    )
    assert result["step_id"] == "radio_review"
    assert [kind for kind, _ in setup_gateway.physical.calls] == ["prepare"]


async def test_manual_fallback_keeps_legacy_payload(hass, setup_gateway):
    """Choosing manual entry displays physical instructions and never sends remote-entry consent.

    @param hass Actual HA instance.
    @param setup_gateway Gateway whose exact Sonoff target supports both methods.
    """
    result = await open_remote_setup(hass, setup_gateway)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"manual_setup": True}
    )
    assert result["step_id"] == "radio_select"
    assert (
        "release both buttons together"
        in result["description_placeholders"]["instructions"]
    )
    assert setup_gateway.physical.calls == []
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        dict(
            target_id="radio-1",
            board_confirmed=True,
            dedicated_radio=True,
            bootloader_ready=True,
        ),
    )
    assert result["step_id"] == "radio_review"
    assert set(setup_gateway.physical.calls[0][1]) == {
        "request_id",
        "instance_id",
        "target_id",
        "candidate_id",
        "dedicated",
        "board_confirmed",
    }


async def test_remote_capability_change_blocks_submission(hass, setup_gateway):
    """A capability removed after display cannot be replaced by stale remote-entry consent.

    @param hass Actual HA instance.
    @param setup_gateway Sonoff target with initially advertised automatic preparation.
    """
    result = await open_remote_setup(hass, setup_gateway)
    setup_gateway.physical.target["entry_methods"] = ["manual"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        dict(
            target_id="radio-1",
            board_confirmed=True,
            dedicated_radio=True,
            entry_confirmed=True,
        ),
    )
    assert result["errors"] == {"base": "bootstrap_incompatible"}
    assert setup_gateway.physical.calls == []


async def test_unrecognized_application_explains_no_entry_and_manual_fallback(
    hass, setup_gateway
):
    """A refusal before entry gives actionable manual setup without suggesting an uncertain transition.

    @param hass Actual HA instance.
    @param setup_gateway Sonoff fixture whose passive identification refuses the current firmware.
    """
    result = await open_remote_setup(hass, setup_gateway)
    physical = setup_gateway.physical
    physical.pending = "prepare"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        dict(
            target_id="radio-1",
            board_confirmed=True,
            dedicated_radio=True,
            entry_confirmed=True,
        ),
    )
    ident = physical.calls[0][1]["request_id"]
    physical.results[ident].update(
        status="rejected",
        error="application_unrecognized",
        plan=None,
        entry_attempted=False,
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    reason = result["description_placeholders"]["reason"]
    assert "No bootloader-entry command was sent" in reason
    assert "manual setup" in reason
    assert "uncertain" not in reason
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"review_again": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"manual_setup": True}
    )
    assert result["step_id"] == "radio_select"
    assert "BOOT" in result["description_placeholders"]["instructions"]
    assert len(physical.calls) == 1


@pytest.mark.parametrize(
    "recovery,supported,manual_failure",
    [
        ("manual_bootloader_inspection_required", True, False),
        ("manual_bootloader_inspection_required", False, False),
        ("flash_recovery_required", True, False),
        ("manual_bootloader_inspection_required", True, True),
    ],
)
async def test_uncertain_entry_requires_fresh_specific_manual_reinspection_capability(
    hass, setup_gateway, recovery, supported, manual_failure
):
    """Only a drained entry-specific recovery permits explicit loader-only inspection without replay.

    @param hass Actual HA instance.
    @param setup_gateway Sonoff fixture with an uncertain original remote preparation.
    @param recovery Entry-only uncertainty or unrelated flash recovery.
    @param supported Whether the daemon currently permits manual reinspection.
    @param manual_failure Whether loader-only inspection fails while the original uncertainty remains.
    """
    result = await open_remote_setup(hass, setup_gateway)
    physical = setup_gateway.physical
    physical.pending = "prepare"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        dict(
            target_id="radio-1",
            board_confirmed=True,
            dedicated_radio=True,
            entry_confirmed=True,
        ),
    )
    ident = physical.calls[0][1]["request_id"]
    physical.results[ident].update(
        status="unknown", recovery=recovery, plan=None, entry_attempted=True
    )
    physical.meta["manual_reinspection_supported"] = supported
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh": True}
    )
    assert result["step_id"] == "radio_progress"
    expected = recovery == "manual_bootloader_inspection_required" and supported
    assert ("manual_reinspection" in result["data_schema"].schema) == expected
    assert [kind for kind, _ in physical.calls] == ["prepare"]
    flow = hass.config_entries.options._progress[result["flow_id"]]
    if expected:
        physical.meta["manual_reinspection_supported"] = False
        denied = await flow.async_step_radio_progress({"manual_reinspection": True})
        assert denied["errors"] == {"base": "bootstrap_unavailable"}
        assert len(physical.calls) == 1
        physical.meta["manual_reinspection_supported"] = True
    shown = await flow.async_step_radio_progress({"manual_reinspection": True})
    if not expected:
        assert shown["errors"] == {"base": "bootstrap_unavailable"}
        assert len(physical.calls) == 1
        return
    assert shown["step_id"] == "radio_reinspect"
    assert_radio_placeholders(shown)
    assert len(physical.calls) == 1
    physical.pending = None
    if manual_failure:
        physical.reject = "prepare"
    shown = await flow.async_step_radio_reinspect(
        dict(
            target_id="radio-1",
            board_confirmed=True,
            dedicated_radio=True,
            bootloader_ready=True,
        )
    )
    assert len(physical.calls) == 2
    original, manual = [body for _, body in physical.calls]
    assert original["request_id"] != manual["request_id"]
    assert "entry_method" not in manual and "entry_confirmed" not in manual
    if not manual_failure:
        assert shown["step_id"] == "radio_review"
        return
    assert shown["step_id"] == "radio_progress"
    assert physical.results[manual["request_id"]]["recovery"] is None
    assert physical.results[ident]["status"] == "unknown"
    physical.meta["active_request_id"] = ident
    physical.target["entry_methods"] = ["manual"]
    hass.config_entries.options.async_abort(result["flow_id"])
    reopened = await open_setup(hass, setup_gateway.entry)
    assert reopened["step_id"] == "radio_progress"
    assert "manual_reinspection" in reopened["data_schema"].schema
    reopened = await hass.config_entries.options.async_configure(
        reopened["flow_id"], {"manual_reinspection": True}
    )
    assert reopened["step_id"] == "radio_reinspect"
    reopened = await hass.config_entries.options.async_configure(
        reopened["flow_id"], {"use_buttons": True}
    )
    assert reopened["step_id"] == "radio_select"
    assert "BOOT" in reopened["description_placeholders"]["instructions"]
    assert len(physical.calls) == 2
    assert physical.results[ident]["status"] == "unknown"


@pytest.mark.parametrize(
    "state",
    [
        "observed",
        "observed_stale_plan",
        "pending",
        "unknown",
        "recovery",
        "different_active",
        "pending_other_active",
    ],
)
async def test_old_cancel_observer_restores_without_replaying_or_hiding_active_work(
    hass, state
):
    """Restore the installed pre-fix observer, healing only an already-observed terminal cancellation.

    @param hass Actual HA instance with its test storage backend.
    @param state Previously observed cancel or an unresolved/competing operation to preserve.
    """
    physical = PhysicalContractFixture(
        network=None, mode="bootstrap", profile="sonoff-zbdongle-e"
    )
    physical.target["entry_methods"] = ["manual", "sonoff_sniffer_0_12"]
    body = dict(
        request_id="old-cancel", instance_id="physical-instance", confirmed=True
    )
    receipt = await physical.submit("cancel", body, "physical-instance")
    receipt["stage"] = "setup_cancelled"
    if state == "unknown":
        receipt["status"] = "unknown"
    if state == "recovery":
        receipt["recovery"] = "manual_bootloader_inspection_required"
    physical.results["old-cancel"] = deepcopy(receipt)
    original_receipt = deepcopy(receipt)
    pending = state in ("pending", "unknown", "pending_other_active")
    saved = dict(receipt, status="pending") if pending else receipt
    url = "http://cancelled.invalid:8090"
    observer = observer_for(hass, url)
    await observer.bind("physical-instance", None)
    await observer.save(
        last_operation=saved,
        pending={"operation": "cancel", "body": body} if pending else None,
        completed_requests=(
            {} if pending else {"cancel": {"body": body, "request_id": "old-cancel"}}
        ),
        dismissed_request_id=None,
        plan=(
            dict(physical.plan, plan_id="old-plan")
            if state == "observed_stale_plan"
            else None
        ),
    )
    hass.data.pop("livolo_ec_bootstrap_observers")
    if state in ("different_active", "pending_other_active"):
        physical.meta["active_request_id"] = "other-active"
        physical.results["other-active"] = dict(
            request_id="other-active",
            kind="prepare",
            status="pending",
            stage="identifying_application",
            recovery=None,
            plan=None,
            transfer_percent=None,
        )
    native = SimpleNamespace(
        info=AsyncMock(side_effect=AssertionError("No normal gateway access")),
        snapshot=AsyncMock(side_effect=AssertionError("No normal gateway access")),
    )
    with (
        patch(
            "custom_components.livolo_ec.bootstrap_flow.BootstrapClient",
            return_value=physical,
        ),
        patch("custom_components.livolo_ec.bootstrap_flow.Client", return_value=native),
    ):
        result = await hass.config_entries.flow.async_init(
            "livolo_ec",
            context={"source": "user"},
            data={"url": url, "token": "secret", "setup_radio": True},
        )
        restored = observer_for(hass, url)
        assert restored is not observer
        assert physical.results["old-cancel"] == original_receipt
        assert [kind for kind, _ in physical.calls] == ["cancel"]
        assert hass.config_entries.async_entries("livolo_ec") == []
        if state in ("observed", "observed_stale_plan"):
            assert result["step_id"] == "radio_detect"
            assert restored.state["dismissed_request_id"] == "old-cancel"
            assert restored.state["last_operation"] == original_receipt
            if state == "observed_stale_plan":
                fresh_flow = hass.config_entries.flow._progress[result["flow_id"]]
                denied = await fresh_flow.async_step_radio_review(
                    {"confirmed": True, "dedicated_radio": True}
                )
                assert denied["errors"] == {"base": "bootstrap_incompatible"}
                assert [kind for kind, _ in physical.calls] == ["cancel"]
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                dict(
                    target_id="radio-1",
                    board_confirmed=True,
                    dedicated_radio=True,
                    entry_confirmed=True,
                ),
            )
            assert result["step_id"] == "radio_review"
            assert [kind for kind, _ in physical.calls] == ["cancel", "prepare"]
            assert physical.calls[-1][1]["request_id"] != "old-cancel"
            assert physical.results["old-cancel"] == original_receipt
        elif state == "pending":
            assert result["type"] == "abort"
            assert result["reason"] == "bootstrap_cancelled"
            assert restored.state["dismissed_request_id"] == "old-cancel"
        else:
            assert restored.state.get("dismissed_request_id") is None
            if state == "pending_other_active":
                assert result["errors"] == {"base": "bootstrap_unavailable"}
            else:
                assert result["step_id"] == "radio_progress"
        native.info.assert_not_awaited()
        native.snapshot.assert_not_awaited()
