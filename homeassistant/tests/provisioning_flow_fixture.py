"""Offline guided-setup prototype; never imported or registered by the HA integration.

This models UI state against Unix's direct-call fake service. It has no HTTP,
serial, subprocess, Home Assistant config-entry or production capability path.
"""

from copy import deepcopy
import uuid


class GuidedSetupPrototype:
    """Exercise explicit review and observation without implying a usable physical installer."""

    def __init__(self, transport, saved=None):
        """Bind an injected fake transport and optional detached observer state.

        @param transport Callable accepting action and payload for the fake model only.
        @param saved JSON-round-trippable prototype state; contains no credentials.
        """
        self.transport = transport
        self.state = deepcopy(saved) if saved else {"step": "start"}

    def _read(self, action, payload=None):
        """Require the simulated contract and the same service on every response.

        @param action Direct-call contract action, never an HTTP path.
        @param payload Optional exact model request body.
        """
        response = self.transport(action, payload)
        if response.get("schema") != 1 or response.get("simulated") is not True:
            raise ValueError("prototype_requires_simulation")
        identity = response.get("instance_id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("instance_missing")
        if self.state.get("instance_id", identity) != identity:
            self.state["step"] = "instance_changed"
            raise ValueError("instance_changed")
        self.state["instance_id"] = identity
        if response.get("ok") is not True:
            raise ValueError(response.get("error") or "invalid_response")
        return response["data"]

    def begin(self):
        """Inspect modeled choices without starting work or constructing a hub entry."""
        if self.state.get("request"):
            return self.refresh()
        info = self._read("info")
        if (
            info.get("operation") != "provision"
            or info.get("physical_install_supported") is not False
            or info.get("model_install_supported") is not True
        ):
            raise ValueError("prototype_capability_missing")
        self.state.update(info=info, choices=self._read("targets"), step="select")
        return deepcopy(self.state)

    def review(self, target_id, candidate_id):
        """Review only a listed target and server-provided artifact, without admission.

        @param target_id Opaque target chosen from this simulated service's list.
        @param candidate_id Opaque candidate chosen by the simulated service.
        """
        if self.state.get("request"):
            raise ValueError("observe_existing_request")
        choices = self.state["choices"]
        if candidate_id != choices["candidate_id"] or target_id not in {
            item["target_id"] for item in choices["targets"]
        }:
            raise ValueError("selection_changed")
        plan = self._read(
            "preflight", {"target_id": target_id, "candidate_id": candidate_id}
        )
        if (
            plan.get("instance_id") != self.state["instance_id"]
            or plan.get("target_id") != target_id
            or plan.get("candidate_id") != candidate_id
            or plan.get("requires_confirmation") is not True
            or plan.get("physical_install_supported") is not False
        ):
            raise ValueError("plan_changed")
        self.state.update(plan=plan, step="review")
        return deepcopy(self.state)

    def confirm_simulation(self, confirmed=False):
        """Submit one reviewed fake job, retaining its identity before any response arrives.

        @param confirmed Explicit confirmation for the simulated experiment only.
        """
        if confirmed is not True:
            return deepcopy(self.state)
        if self.state.get("request"):
            return self.refresh()
        plan = self.state["plan"]
        self.state["request"] = dict(
            request_id=uuid.uuid4().hex,
            plan_id=plan["plan_id"],
            instance_id=self.state["instance_id"],
            confirmed=True,
        )
        self.state["step"] = "observing"
        return self._observe(self._read("start", self.state["request"]))

    def refresh(self):
        """Read the same durable job after response loss or observer reconstruction."""
        request = self.state["request"]
        return self._observe(self._read("job", {"request_id": request["request_id"]}))

    def _observe(self, job):
        """Stop at verified firmware; never equate it to configured or ready HA devices.

        @param job Authoritative job returned by the fake service.
        """
        plan = self.state["plan"]
        if (
            job.get("request_id") != self.state["request"]["request_id"]
            or job.get("instance_id") != self.state["instance_id"]
            or any(
                job.get(key) != plan.get(key)
                for key in (
                    "plan_id",
                    "target_id",
                    "candidate_id",
                    "artifact_sha256",
                    "profile",
                    "version",
                )
            )
            or job.get("network_ready") is not False
            or job.get("setup_required") is not True
        ):
            raise ValueError("job_changed")
        if job.get("recovery") or job.get("status") in ("unknown", "rejected"):
            step = "inspect_recovery"
        elif (
            job.get("status") == "confirmed" and job.get("application_verified") is True
        ):
            step = "firmware_verified_setup_required"
        else:
            step = "observing"
        self.state.update(job=job, step=step)
        return deepcopy(self.state)
