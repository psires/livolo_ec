"""Native guided radio setup with explicit physical actions and durable observation."""

import re
import uuid

import voluptuous as vol
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ._client import (
    AuthenticationError,
    Client,
    IncompatibleError,
    LivoloError,
    network_id,
)
from ._client.bootstrap import BootstrapClient, BootstrapError, SNIFFER_ENTRY_METHOD
from .bootstrap_observer import observer_for
from .const import CONF_TOKEN, CONF_URL

BOARD_INSTRUCTIONS = {
    "zb-gw04-1v2": (
        "ZB-GW04 1v2",
        "Keep USB power connected. Hold BOOT, press and release RESET, "
        "keep holding BOOT for three seconds, then release.",
    ),
    # SONOFF's ZBDongle-E flashing guide specifies releasing both buttons together.
    "sonoff-zbdongle-e": (
        "Sonoff ZBDongle-E",
        "Keep USB power connected. Hold BOOT, press RESET, then release both "
        "buttons together. Use the ZBDongle-E model identified on its label.",
    ),
}


def display(value):
    """Escape bounded public text before rendering it inside HA Markdown.

    @param value Public response text or absent value.
    """
    text = str(value)[:512] if value is not None else "Not reported"
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", text)


class BootstrapFlowMixin:
    """Share guided setup across a new hub and an existing hub's options dialog."""

    def _radio_entry(self):
        """Return an existing options entry without creating a provisional hub."""
        return getattr(self, "config_entry", None)

    def _radio_clients(self):
        """Use submitted or existing credentials without exposing them in UI/storage."""
        entry = self._radio_entry()
        data = entry.data if entry else self._bootstrap_settings
        native = Client(
            async_get_clientsession(self.hass), data[CONF_URL], data[CONF_TOKEN]
        )
        observer = observer_for(
            self.hass, data[CONF_URL], entry.entry_id if entry else None
        )
        return native, BootstrapClient(native), observer

    def _radio_form(self, step, fields=None, errors=None, **details):
        """Render an explicit action or observation form with bounded public evidence.

        @param step Registered native flow step identifier.
        @param fields Optional voluptuous input schema fields.
        @param errors Localized form errors.
        @param details Public description placeholders.
        """
        fields = dict(fields or {})
        if getattr(self, "_radio_cancel_supported", False) and step in {
            "radio_setup",
            "radio_select",
            "radio_detect",
            "radio_reinspect",
            "radio_review",
            "radio_progress",
            "radio_initialize",
            "radio_handoff",
        }:
            fields[vol.Optional("cancel_setup", default=False)] = bool
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(fields),
            errors=errors or {},
            description_placeholders={
                key: display(value) for key, value in details.items()
            },
        )

    def _radio_failure(self, error):
        """Explain failure without inventing successful mutation or clearing intent.

        @param error Typed transport, capability or durable request failure.
        """
        code = (
            "invalid_auth"
            if isinstance(error, AuthenticationError)
            else (
                "bootstrap_incompatible"
                if isinstance(error, IncompatibleError)
                else "bootstrap_unavailable"
            )
        )
        return self._radio_form(
            "radio_setup",
            {vol.Optional("refresh", default=True): bool},
            {"base": code},
            reason=getattr(
                error, "code", "Connection or identity could not be verified"
            ),
        )

    async def _radio_info(self, client, observer):
        """Bind physical-service identity and reject replacement of an existing network.

        @param client Physical bootstrap client.
        @param observer Shared durable observer held under its lock.
        """
        await observer.restore()
        info = await client.info(observer.state.get("instance_id"))
        if info is None:
            raise BootstrapError("bootstrap_not_supported")
        self._radio_cancel_supported = info.get("cancel_supported") is True
        entry = self._radio_entry()
        expected = entry.unique_id if entry else info["expected_network_id"]
        if entry and info["expected_network_id"] != expected:
            raise IncompatibleError("Bootstrap does not preserve this hub's network")
        await observer.bind(info["instance_id"], expected)
        return info

    async def _radio_accept(self, observer, result):
        """Reconcile a response against the reviewed request before advancing UI state.

        @param observer Shared observer held under its lock.
        @param result Validated durable physical operation result.
        """
        state = observer.state
        pending = state.get("pending")
        if pending and (
            result["request_id"] != pending["body"]["request_id"]
            or result["kind"] != pending["operation"]
        ):
            raise IncompatibleError("Bootstrap request changed")
        kind = result["kind"]
        fields = {"last_operation": result}
        if kind == "prepare" and result["status"] == "confirmed":
            plan = result["plan"]
            selection = state.get("selection")
            if selection and any(
                plan[key] != selection[key]
                for key in ("target_id", "candidate_id", "artifact_sha256", "version")
            ):
                raise IncompatibleError("Prepared image or target changed")
            if selection and plan["profile"] != selection["board"]:
                raise IncompatibleError("Prepared board changed")
            fields["plan"] = plan
        if kind == "install":
            plan = state.get("plan")
            admission_rejected = (
                result["status"] == "rejected"
                and result["stage"] == "rejected"
                and not result.get("recovery")
                and not result.get("application_verified")
                and result.get("transfer_percent") is None
            )
            if plan and any(
                result.get(key) != plan[key]
                and not (admission_rejected and result.get(key) is None)
                for key in (
                    "plan_id",
                    "target_id",
                    "candidate_id",
                    "artifact_sha256",
                    "profile",
                    "version",
                )
            ):
                raise IncompatibleError("Installation does not match the reviewed plan")
            fields["install"] = result
        if kind == "handoff":
            install = state.get("install")
            if install and result.get("install_request_id") != install["request_id"]:
                raise IncompatibleError("Handoff refers to a different installation")
        if kind == "initialize" and result["status"] == "confirmed":
            chosen = result.get("network")
            if not chosen:
                raise IncompatibleError("Initialization did not report a network")
            identity = network_id(chosen)
            if state.get("expected_network_id") not in (None, identity):
                raise IncompatibleError("Initialization changed the existing network")
            fields["expected_network_id"] = identity
        if result["status"] in ("confirmed", "rejected"):
            fields["pending"] = None
            if pending:
                completed = dict(state.get("completed_requests") or {})
                completed[kind] = {
                    "body": pending["body"],
                    "request_id": result["request_id"],
                    "confirmation_id": pending.get("confirmation_id"),
                }
                fields["completed_requests"] = completed
        await observer.save(**fields)

    async def async_step_radio_setup(self, user_input=None):
        """Resume existing work or inspect capabilities; opening never sends a POST.

        @param user_input Optional request to refresh status after a connection failure.
        """
        if user_input and user_input.get("cancel_setup"):
            return await self.async_step_radio_cancel()
        try:
            _, client, observer = self._radio_clients()
            async with observer.lock:
                info = await self._radio_info(client, observer)
                pending = observer.state.get("pending")
                request = (
                    pending["body"]["request_id"]
                    if pending
                    else (info["active_request_id"] or info["latest_request_id"])
                )
                if request and (
                    request != observer.state.get("dismissed_request_id")
                    or info.get("manual_reinspection_supported") is True
                    and request == info["active_request_id"]
                ):
                    previous = observer.state.get("last_operation") or {}
                    resume_completed_cancel = (
                        pending is None
                        and previous.get("request_id") == request
                        and previous.get("kind") == "cancel"
                        and previous.get("status") == "confirmed"
                        and previous.get("cancelled_unconfigured") is True
                        and not previous.get("recovery")
                    )
                    result = await client.operation(request, info["instance_id"])
                    await self._radio_accept(observer, result)
                    if (
                        result["kind"] == "enter"
                        and result["status"] == "confirmed"
                        and not result.get("recovery")
                    ):
                        return await self._radio_select_form(observer)
                    return await self._radio_route(
                        info,
                        observer,
                        result,
                        resume_completed_cancel=resume_completed_cancel,
                    )
                if info["mode"] == "gateway":
                    if not info["enter_supported"]:
                        raise BootstrapError(
                            info["blocked_reason"] or "setup_mode_not_supported"
                        )
                    return self._radio_form(
                        "radio_enter",
                        {vol.Required("confirmed", default=False): bool},
                        network=info["expected_network_id"],
                    )
            return await self.async_step_radio_select()
        except (LivoloError, OSError) as error:
            return self._radio_failure(error)

    async def _radio_route(self, info, observer, result, resume_completed_cancel=False):
        """Choose the next visible step from durable results, not local elapsed time.

        @param info Current validated service metadata.
        @param observer Observer locked by the caller.
        @param result Last validated operation.
        @param resume_completed_cancel Whether HA had already observed this completed new-hub cancellation before this flow.
        """
        if result["status"] != "confirmed" or result.get("recovery"):
            if (
                result["kind"] == "install" and result["status"] != "rejected"
            ) or result.get("recovery"):
                self._radio_cancel_supported = False
            return self._radio_progress(
                result,
                manual_reinspection_supported=info.get("manual_reinspection_supported")
                is True,
            )
        if result["kind"] == "prepare":
            plan = result["plan"]
            if (
                plan["profile"] not in BOARD_INSTRUCTIONS
                or plan["profile"] != info["profile"]
            ):
                raise BootstrapError("unsupported_board_instructions")
            self._radio_reviewed_plan = plan["plan_id"]
            return self._radio_form(
                "radio_review",
                {
                    vol.Required("confirmed", default=False): bool,
                    vol.Required("dedicated_radio", default=False): bool,
                    vol.Optional("inspect_again", default=False): bool,
                },
                board=plan["profile"],
                version=plan["version"],
                network_effect=(
                    "Your existing Livolo network and paired devices are retained."
                    if info.get("baseline_available") is True
                    else "No previous Livolo gateway was verified. This replaces the "
                    "current application, including sniffer firmware. HA adds this hub "
                    "only after separate network setup and verification."
                ),
                qualification=(
                    "Engineering test only; not a qualified public firmware release"
                    if any(
                        value.get("engineering_only") is True
                        for value in (
                            info,
                            plan,
                            result,
                            observer.state.get("selection") or {},
                        )
                    )
                    else "Check the exact board's release qualification before installing."
                ),
            )
        if result["kind"] in ("handoff", "cancel"):
            if (
                result["kind"] == "cancel"
                and result.get("cancelled_unconfigured") is True
                and not self._radio_entry()
                and result.get("network_ready") is False
            ):
                if info.get("active_request_id") not in (None, result["request_id"]):
                    raise BootstrapError("setup_operation_active")
                await observer.save(dismissed_request_id=result["request_id"])
                if resume_completed_cancel:
                    return await self._radio_select_form(observer)
                return self.async_abort(reason="bootstrap_cancelled")
            if result.get("network_ready") is not True:
                raise IncompatibleError("Handoff has not confirmed network readiness")
            return await self._radio_finish(observer)
        if result["kind"] in ("install", "initialize"):
            if (
                info["expected_network_id"] is None
                and observer.state.get("expected_network_id") is None
            ):
                if not info["initialize_supported"]:
                    raise BootstrapError("network_initialization_unavailable")
                return self._radio_form(
                    "radio_initialize", {vol.Required("confirmed", default=False): bool}
                )
            return self._radio_form(
                "radio_handoff",
                {vol.Required("confirmed", default=False): bool},
                network=observer.state["expected_network_id"],
            )
        return self._radio_form(
            "radio_progress",
            {vol.Optional("refresh", default=True): bool},
            status="Setup mode entered",
            stage="Select the radio next",
            progress="Not started",
            reason="None",
        )

    @staticmethod
    def _radio_can_reinspect(result):
        """Allow explicit inspection after a clean inspection/install refusal or mode check.

        @param result Authoritative terminal result, without unresolved local intent.
        """
        return (
            result.get("status") == "rejected"
            and not result.get("recovery")
            and (
                result.get("kind") in ("prepare", "install")
                or result.get("kind") == "cancel"
                and result.get("error") == "inspect_required"
            )
        )

    def _radio_progress(self, result, manual_reinspection_supported=False):
        """Show a durable job without equating accepted or transferred with verified.

        @param result Validated physical operation state.
        @param manual_reinspection_supported Server confirms the entry worker ended and manual recovery is safe.
        """
        fields = {vol.Optional("refresh", default=True): bool}
        if self._radio_can_reinspect(result):
            fields[vol.Optional("review_again", default=False)] = bool
        if self._radio_can_recover_entry(result, manual_reinspection_supported):
            fields[vol.Optional("manual_reinspection", default=False)] = bool
        reason = result.get("recovery") or result.get("error") or "None"
        reason = {
            "inspect_required": "Before resuming the previous firmware, use Return to radio selection, follow BOOT/RESET and Inspect, then choose Cancel setup. Inspection does not install firmware.",
            "plan_expired": "The review expired. Return to radio selection and inspect again before choosing Install.",
            "software_rollback_required": "The previous firmware was restored, but normal operation could not be verified. Restore the matching Livolo app version before resuming this hub.",
            "manual_bootloader_inspection_required": "The bootloader transition could not be verified. Device mode is uncertain; no firmware was transferred by this preparation request. Automatic entry will not be repeated. Use manual inspection only when it is offered below.",
            "application_unrecognized": "The supported sniffer could not be verified. No bootloader-entry command was sent. Return to radio selection and choose manual setup to inspect the bootloader explicitly.",
        }.get(reason, reason)
        return self._radio_form(
            "radio_progress",
            fields,
            status=result["status"],
            stage={
                "identifying_application": "Identifying the supported sniffer; no entry command sent yet",
                "bootloader_entry": "Requesting bootloader entry; stopping capture",
                "verifying_loader": "Verifying the bootloader before firmware review",
            }.get(result["stage"], result["stage"]),
            progress=(
                f"{result['transfer_percent']:g}% image transfer; verification still required"
                if result["transfer_percent"] is not None
                else "Stage only; no measured percentage"
            ),
            reason=reason,
        )

    @staticmethod
    def _radio_can_recover_entry(result, supported):
        """Allow only explicit loader inspection after a server-fenced entry uncertainty.

        @param result Latest validated preparation result.
        @param supported Fresh server capability confirming safe manual inspection.
        """
        return bool(
            supported is True
            and result.get("kind") == "prepare"
            and result.get("status") in ("unknown", "rejected")
            and result.get("recovery") == "manual_bootloader_inspection_required"
            and result.get("entry_attempted") is True
            and not result.get("plan")
            and not result.get("application_verified")
            and result.get("transfer_percent") is None
        )

    async def async_step_radio_progress(self, user_input=None):
        """Refresh the saved job or explicitly return to review after a clean rejection.

        @param user_input Optional refresh or review-again action, never an install retry.
        """
        if user_input and user_input.get("cancel_setup"):
            return await self.async_step_radio_cancel()
        try:
            _, client, observer = self._radio_clients()
            async with observer.lock:
                await observer.restore()
                last = observer.state.get("last_operation") or {}
                if user_input and user_input.get("manual_reinspection"):
                    info = await self._radio_info(client, observer)
                    pending = observer.state.get("pending")
                    request_id = (
                        pending["body"]["request_id"]
                        if pending
                        else last.get("request_id")
                    )
                    if not request_id:
                        raise BootstrapError("manual_reinspection_unavailable")
                    last = await client.operation(request_id, info["instance_id"])
                    await self._radio_accept(observer, last)
                    if not self._radio_can_recover_entry(
                        last, info.get("manual_reinspection_supported")
                    ):
                        raise BootstrapError("manual_reinspection_unavailable")
                    self._radio_manual = True
                    await observer.save(
                        dismissed_request_id=last["request_id"],
                        pending=None,
                        plan=None,
                        install=None,
                        completed_requests={},
                    )
                    return await self._radio_select_form(observer)
                if (
                    user_input
                    and user_input.get("review_again")
                    and self._radio_can_reinspect(last)
                ):
                    await observer.save(
                        dismissed_request_id=last["request_id"],
                        pending=None,
                        plan=None,
                        install=None,
                        completed_requests={},
                    )
                    return await self._radio_select_form(observer)
                if last.get("kind") == "enter" and last.get("status") == "confirmed":
                    return await self._radio_select_form(observer)
            return await self.async_step_radio_setup()
        except (LivoloError, OSError) as error:
            return self._radio_failure(error)

    async def _radio_select_form(self, observer):
        """Read exact-board choices while keeping physical inspection explicit.

        @param observer Locked durable observer.
        """
        _, client, _ = self._radio_clients()
        info = await self._radio_info(client, observer)
        if not info["prepare_supported"] and not (
            getattr(self, "_radio_manual", False)
            and info.get("manual_reinspection_supported") is True
        ):
            raise BootstrapError(
                info["blocked_reason"] or "radio_inspection_unavailable"
            )
        choices = await client.targets(info["instance_id"])
        if not choices["targets"]:
            raise BootstrapError(
                choices["blocked_reason"] or "no_supported_radio_or_image"
            )
        if info["profile"] not in BOARD_INSTRUCTIONS or any(
            item["board"] != info["profile"] for item in choices["targets"]
        ):
            raise BootstrapError("unsupported_board_instructions")
        self._radio_choices = {item["target_id"]: item for item in choices["targets"]}
        self._radio_prepare_review_id = uuid.uuid4().hex
        automatic = (
            not getattr(self, "_radio_manual", False)
            and info.get("manual_reinspection_supported") is not True
        ) and any(
            SNIFFER_ENTRY_METHOD in item.get("entry_methods", [])
            and item["board"] == "sonoff-zbdongle-e"
            for item in choices["targets"]
        )
        fields = {
            vol.Optional("target_id"): vol.In(
                {
                    key: f"{item['label']} ({item['board']})"
                    for key, item in self._radio_choices.items()
                }
            ),
            vol.Required("board_confirmed", default=False): bool,
            vol.Required("dedicated_radio", default=False): bool,
        }
        if automatic:
            fields[vol.Required("entry_confirmed", default=False)] = bool
            fields[vol.Optional("manual_setup", default=False)] = bool
            return self._radio_form(
                "radio_detect", fields, board=BOARD_INSTRUCTIONS[info["profile"]][0]
            )
        fields[vol.Required("bootloader_ready", default=False)] = bool
        if (
            info.get("manual_reinspection_supported") is True
            and getattr(self, "_radio_manual", False)
            and not getattr(self, "_radio_use_buttons", False)
        ):
            fields[vol.Optional("use_buttons", default=False)] = bool
            return self._radio_form(
                "radio_reinspect", fields, board=BOARD_INSTRUCTIONS[info["profile"]][0]
            )
        return self._radio_form(
            "radio_select",
            fields,
            board=BOARD_INSTRUCTIONS[info["profile"]][0],
            instructions=BOARD_INSTRUCTIONS[info["profile"]][1],
        )

    async def async_step_radio_select(self, user_input=None):
        """Select and acknowledge the exact board before any loader inspection.

        @param user_input Explicit target, board identification and bootloader readiness.
        """
        return await self._radio_prepare(user_input, automatic=False)

    async def async_step_radio_detect(self, user_input=None):
        """Offer explicit supported-sniffer entry or switch to manual instructions without I/O.

        @param user_input Explicit remote-entry consent or manual fallback selection.
        """
        if user_input and user_input.get("cancel_setup"):
            return await self.async_step_radio_cancel()
        if user_input and user_input.get("manual_setup"):
            self._radio_manual = True
            return await self.async_step_radio_select()
        return await self._radio_prepare(user_input, automatic=True)

    async def async_step_radio_reinspect(self, user_input=None):
        """Inspect an uncertain current loader state first, with optional physical fallback.

        @param user_input Explicit loader-only inspection or request for button instructions.
        """
        if user_input and user_input.get("cancel_setup"):
            return await self.async_step_radio_cancel()
        if user_input and user_input.get("use_buttons"):
            self._radio_use_buttons = True
            return await self.async_step_radio_select()
        return await self._radio_prepare(user_input, automatic=False)

    async def _radio_prepare(self, user_input, automatic):
        """Submit one inspected target with distinct manual or remote-entry consent.

        @param user_input Explicit target, exact board, dedication and method-specific consent.
        @param automatic Whether this form authorizes supported-sniffer software entry.
        """
        if user_input and user_input.get("cancel_setup"):
            return await self.async_step_radio_cancel()
        try:
            _, client, observer = self._radio_clients()
            async with observer.lock:
                if (
                    not user_input
                    or user_input.get("board_confirmed") is not True
                    or user_input.get("dedicated_radio") is not True
                    or user_input.get(
                        "entry_confirmed" if automatic else "bootloader_ready"
                    )
                    is not True
                ):
                    return await self._radio_select_form(observer)
                selected = getattr(self, "_radio_choices", {}).get(
                    user_input.get("target_id")
                )
                if not selected:
                    raise IncompatibleError("Select a currently displayed radio")
                info = await self._radio_info(client, observer)
                if not info["prepare_supported"] and not (
                    not automatic and info.get("manual_reinspection_supported") is True
                ):
                    raise BootstrapError("radio_inspection_unavailable")
                choices = await client.targets(info["instance_id"])
                if (
                    selected not in choices["targets"]
                    or info["profile"] != selected["board"]
                    or selected["board"] not in BOARD_INSTRUCTIONS
                    or any(
                        item["board"] != info["profile"] for item in choices["targets"]
                    )
                ):
                    raise IncompatibleError("Radio or image selection changed")
                if automatic and (
                    selected["board"] != "sonoff-zbdongle-e"
                    or SNIFFER_ENTRY_METHOD not in selected.get("entry_methods", [])
                    or info.get("manual_reinspection_supported") is True
                ):
                    raise BootstrapError("software_entry_unavailable")
                for configured in self.hass.config_entries.async_entries("zha"):
                    device = configured.data.get("device")
                    if (
                        configured.disabled_by is None
                        and isinstance(device, dict)
                        and device.get("path") in selected.get("port_aliases", [])
                    ):
                        raise BootstrapError("known_zha_conflict")
                await observer.save(selection=selected)
                payload = {
                    "target_id": selected["target_id"],
                    "candidate_id": selected["candidate_id"],
                    "dedicated": True,
                    "board_confirmed": True,
                }
                if automatic:
                    payload.update(
                        entry_method=SNIFFER_ENTRY_METHOD, entry_confirmed=True
                    )
                return await self._radio_submit_locked(
                    "prepare",
                    payload,
                    info,
                    observer,
                )
        except (LivoloError, OSError) as error:
            return self._radio_failure(error)

    async def _radio_action(self, operation, user_input):
        """Submit a confirmed lifecycle action under one shared observer lock.

        @param operation Enter, install, initialize or handoff.
        @param user_input Explicit confirmation form submission.
        """
        if user_input and user_input.get("cancel_setup"):
            return await self.async_step_radio_cancel()
        if (
            not user_input
            or user_input.get("confirmed") is not True
            or operation == "install"
            and user_input.get("dedicated_radio") is not True
        ):
            return await self.async_step_radio_setup()
        try:
            _, client, observer = self._radio_clients()
            async with observer.lock:
                info = await self._radio_info(client, observer)
                payload = {"confirmed": True}
                if operation == "enter" and observer.state.get("finished"):
                    await observer.save(
                        completed_requests={},
                        plan=None,
                        install=None,
                        last_operation=None,
                        finished=False,
                    )
                if operation == "install":
                    plan = observer.state.get("plan") or {}
                    if plan.get("plan_id") != getattr(
                        self, "_radio_reviewed_plan", None
                    ):
                        raise IncompatibleError(
                            "Review an eligible current installation plan first"
                        )
                    payload["plan_id"] = plan["plan_id"]
                if operation == "handoff":
                    install = observer.state.get("install") or {}
                    if (
                        install.get("status") != "confirmed"
                        or install.get("application_verified") is not True
                    ):
                        raise IncompatibleError("Firmware has not been verified")
                    payload.update(
                        install_request_id=install["request_id"], network=None
                    )
                return await self._radio_submit_locked(
                    operation, payload, info, observer
                )
        except (LivoloError, OSError) as error:
            return self._radio_failure(error)

    async def _radio_submit_locked(self, operation, payload, info, observer):
        """Persist one request then submit it once; lost replies retain unresolved intent.

        @param operation Explicit physical operation.
        @param payload Reviewed operation parameters.
        @param info Fresh physical-service capabilities.
        @param observer Shared observer held under its lock.
        """
        _, client, _ = self._radio_clients()
        bound = dict(payload, instance_id=info["instance_id"])
        confirmation_id = (
            getattr(self, "_radio_prepare_review_id", None)
            if operation == "prepare"
            else (
                getattr(self, "_radio_cancel_review_id", None)
                if operation == "cancel"
                else None
            )
        )
        completed = (observer.state.get("completed_requests") or {}).get(operation)
        if observer.state.get("pending"):
            pending = observer.state["pending"]
            result = await client.operation(
                pending["body"]["request_id"], info["instance_id"]
            )
        elif (
            completed
            and completed.get("confirmation_id") == confirmation_id
            and {
                key: value
                for key, value in completed["body"].items()
                if key != "request_id"
            }
            == bound
        ):
            result = await client.operation(
                completed["request_id"], info["instance_id"]
            )
        else:
            capability = {
                "enter": "enter_supported",
                "prepare": "prepare_supported",
                "install": "physical_install_supported",
                "initialize": "initialize_supported",
                "cancel": "cancel_supported",
            }.get(operation)
            if capability and info.get(capability) is not True:
                raise BootstrapError("operation_not_supported")
            body = await observer.intent(operation, bound, confirmation_id)
            result = await client.submit(operation, body, info["instance_id"])
        await self._radio_accept(observer, result)
        # An enter/initialize/handoff may have changed mode or network metadata.
        if result["kind"] == "initialize" and result["status"] == "confirmed":
            info = dict(info, expected_network_id=observer.state["expected_network_id"])
        return await self._radio_route(info, observer, result)

    async def async_step_radio_enter(self, user_input=None):
        """Explicitly stop normal radio service and enter setup mode.

        @param user_input User confirmation acknowledging temporary device downtime.
        """
        return await self._radio_action("enter", user_input)

    async def async_step_radio_review(self, user_input=None):
        """Explicitly confirm installation of the reviewed exact image.

        @param user_input User confirmation after board, version and digest review.
        """
        if user_input and user_input.get("inspect_again"):
            return await self.async_step_radio_select()
        return await self._radio_action("install", user_input)

    async def async_step_radio_initialize(self, user_input=None):
        """Explicitly initialize a new network without inventing a ready hub.

        @param user_input User consent to daemon-generated valid network settings.
        """
        return await self._radio_action("initialize", user_input)

    async def async_step_radio_handoff(self, user_input=None):
        """Explicitly resume the normal daemon after verified firmware.

        @param user_input User confirmation of preserved or initialized network settings.
        """
        return await self._radio_action("handoff", user_input)

    async def async_step_radio_cancel(self, user_input=None):
        """Explicitly request safe baseline restoration, never clear recovery locally.

        @param user_input Explicit cancellation confirmation.
        """
        if user_input and user_input.get("confirmed") is True:
            return await self._radio_action("cancel", user_input)
        try:
            _, client, observer = self._radio_clients()
            async with observer.lock:
                info = await self._radio_info(client, observer)
                if info.get("cancel_supported") is not True:
                    raise BootstrapError("safe_cancel_unavailable")
                if info.get("baseline_available") is True:
                    board = BOARD_INSTRUCTIONS.get(info["profile"])
                    if board is None:
                        raise BootstrapError("unsupported_board_instructions")
                    instructions = (
                        board[1] + " Confirm only after this sequence. The daemon "
                        "checks the bootloader and previous firmware before resuming "
                        "your existing hub. No firmware catalog or installation is needed."
                    )
                else:
                    instructions = (
                        "No previous Livolo gateway was verified. Cancellation leaves "
                        "setup unfinished and creates no HA hub. It does not restore "
                        "or verify the previous sniffer or other application. No "
                        "BOOT/RESET action is needed to cancel this setup. The radio "
                        "may remain in bootloader mode."
                    )
                self._radio_cancel_review_id = uuid.uuid4().hex
                return self._radio_form(
                    "radio_cancel",
                    {vol.Required("confirmed", default=False): bool},
                    instructions=instructions,
                )
        except (LivoloError, OSError) as error:
            return self._radio_failure(error)

    async def _radio_finish(self, observer):
        """Require actual ready network agreement before creating or finishing an entry.

        @param observer Observer containing the expected retained network identity.
        """
        native, _, _ = self._radio_clients()
        info = await native.info()
        snapshot = await native.snapshot()
        identity = network_id(info)
        if (
            identity != network_id(snapshot["data"])
            or identity != observer.state.get("expected_network_id")
            or snapshot["data"].get("ready") is not True
            or info.get("simulated") is True
            or snapshot["data"].get("simulated") is True
        ):
            raise IncompatibleError(
                "Normal radio network readiness could not be verified"
            )
        entry = self._radio_entry()
        if entry:
            if identity != entry.unique_id:
                raise IncompatibleError("The existing hub network changed")
            await observer.save(
                dismissed_request_id=observer.state["last_operation"]["request_id"],
                finished=True,
            )
            return self.async_create_entry(title="", data=dict(entry.options))
        await self.async_set_unique_id(identity)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Livolo EC {info['gateway']}", data=dict(self._bootstrap_settings)
        )
