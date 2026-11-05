"""UI configuration, credential repair and bounded network management."""

import re

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from ._client import (
    AuthenticationError,
    Client,
    CommandFailed,
    IncompatibleError,
    LivoloError,
    network_id,
    normalize_url,
)
from .const import CONF_ALLOW_SIMULATED, CONF_TOKEN, CONF_URL, DOMAIN


def connection_schema(defaults):
    """Build the endpoint form without echoing stored credentials.

    @param defaults Existing non-secret connection settings.
    @return Schema for a connection or credential-repair step.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_URL, default=defaults.get(CONF_URL, "http://localhost:8090")
            ): str,
            vol.Required(CONF_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_ALLOW_SIMULATED, default=defaults.get(CONF_ALLOW_SIMULATED, False)
            ): bool,
        }
    )


class LivoloConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect to an existing daemon; never open or flash serial during discovery."""

    VERSION = 1

    def __init__(self):
        """Initialize optional discovered connection hints."""
        self._defaults = {}

    async def _connection(self, step, user_input):
        """Validate a live API endpoint and prevent accidental network replacement.

        @param step Flow step for the form.
        @param user_input Submitted settings, or None to display a form.
        @return Config flow result.
        """
        errors = {}
        if user_input is not None:
            try:
                data = dict(user_input)
                data[CONF_URL] = normalize_url(data[CONF_URL])
                client = Client(
                    async_get_clientsession(self.hass), data[CONF_URL], data[CONF_TOKEN]
                )
                info = await client.info()
                envelope = await client.snapshot()
                identity = network_id(info)
                if identity != network_id(envelope["data"]):
                    raise IncompatibleError("Network changed during validation")
                if (
                    info.get("simulated") or envelope["data"].get("simulated")
                ) and not data.get(CONF_ALLOW_SIMULATED):
                    errors["base"] = "simulation_disabled"
                else:
                    await self.async_set_unique_id(identity)
                    if step in ("reconfigure", "reauth_confirm"):
                        entry = (
                            self._get_reconfigure_entry()
                            if step == "reconfigure"
                            else self._get_reauth_entry()
                        )
                        if entry.unique_id != identity:
                            return self.async_abort(reason="wrong_network")
                        return self.async_update_reload_and_abort(entry, data=data)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Livolo EC {info['gateway']}", data=data
                    )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except IncompatibleError:
                errors["base"] = "incompatible"
            except LivoloError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_url"
        return self.async_show_form(
            step_id=step, data_schema=connection_schema(self._defaults), errors=errors
        )

    async def async_step_user(self, user_input=None):
        """Configure the authenticated daemon manually.

        @param user_input Submitted connection settings.
        """
        return await self._connection("user", user_input)

    async def async_step_hassio(self, discovery_info):
        """Offer a discovered app endpoint while still requiring user credentials.

        @param discovery_info Supervisor service information.
        """
        config = discovery_info.config
        self._defaults = {CONF_URL: config.get("url", "http://localhost:8090")}
        return await self.async_step_user()

    async def async_step_reconfigure(self, user_input=None):
        """Change the endpoint or credential without changing network identity.

        @param user_input Replacement connection settings.
        """
        self._defaults = self._get_reconfigure_entry().data
        return await self._connection("reconfigure", user_input)

    async def async_step_reauth(self, entry_data):
        """Start credential repair for an existing entry.

        @param entry_data Previous entry settings.
        """
        self._defaults = entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate replacement credentials against the original network.

        @param user_input Replacement connection settings.
        """
        return await self._connection("reauth_confirm", user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Expose targeted commissioning actions through HA's Configure dialog.

        @param config_entry Entry whose daemon should receive the operations.
        """
        return LivoloOptionsFlow()


class LivoloOptionsFlow(OptionsFlow):
    """Use the daemon's device registry and bounded pairing windows."""

    def __init__(self):
        """Keep the reviewed discovery only for this commissioning flow."""
        self._review = None

    def _client(self):
        """Use the configured daemon without owning an HTTP session."""
        data = self.config_entry.data
        return Client(
            async_get_clientsession(self.hass), data[CONF_URL], data[CONF_TOKEN]
        )

    async def _discoveries(self):
        """Fetch current unregistered candidates, including unsupported observations.

        @return Candidate mapping and current radio readiness.
        """
        data = (await self._client().snapshot())["data"]
        if network_id(data) != self.config_entry.unique_id:
            raise IncompatibleError("The daemon network changed")
        registered = {device["id"] for device in data["devices"]}
        candidates = {
            item["device"]: item
            for item in data.get("discoveries", [])
            if isinstance(item, dict)
            and isinstance(item.get("device"), str)
            and re.fullmatch(r"[0-9a-f]{8}", item["device"])
            and not item.get("configured")
            and item["device"] not in registered
        }
        return candidates, data["ready"]

    @staticmethod
    def _detected_profile(candidate):
        """Accept only a complete server-classified profile, never infer output count.

        @param candidate Current discovery record, or an empty mapping.
        @return Valid detected profile or None for unavailable evidence.
        """
        profile = candidate.get("profile")
        if (
            isinstance(profile, dict)
            and profile.get("status") == "detected"
            and type(profile.get("outputs")) is int
            and profile["outputs"] in (1, 2)
            and isinstance(profile.get("suffix"), str)
            and re.fullmatch(r"[0-9a-f]{4}", profile["suffix"])
            and isinstance(profile.get("token"), str)
            and profile["token"]
        ):
            return profile
        return None

    async def async_step_init(self, user_input=None):
        """Show management actions without changing user names or areas.

        @param user_input Unused menu input.
        """
        return self.async_show_menu(
            step_id="init",
            menu_options=["add", "discover", "pair", "refresh", "forget"],
        )

    async def async_step_discover(self, user_input=None):
        """Refresh observed candidates or review one explicitly selected switch.

        @param user_input Optional candidate selection or manual setup request.
        """
        self._review = None
        if user_input and user_input.get("manual"):
            return await self.async_step_add()
        errors = {}
        candidates = {}
        try:
            candidates, ready = await self._discoveries()
            if user_input and user_input.get("device"):
                device = user_input["device"]
                profile = self._detected_profile(candidates.get(device, {}))
                if ready and profile:
                    self._review = dict(device=device, **profile)
                    return await self.async_step_detected()
                errors["base"] = "detection_unavailable"
            elif not ready:
                errors["base"] = "radio_not_ready"
        except AuthenticationError:
            errors["base"] = "invalid_auth"
        except IncompatibleError:
            errors["base"] = "incompatible"
        except LivoloError:
            errors["base"] = "cannot_connect"
        return self._discovery_form(candidates, errors)

    def _discovery_form(self, candidates, errors):
        """Offer selection, an explicit refresh, and a manual compatibility fallback.

        @param candidates Unregistered observations from a fresh API snapshot.
        @param errors Form-level failures to display.
        """
        fields = {}
        if candidates:
            options = []
            for device, candidate in sorted(candidates.items()):
                profile = self._detected_profile(candidate)
                if profile:
                    label = f"{device} — {profile['outputs']} output(s), experimental"
                else:
                    status = (candidate.get("profile") or {}).get("status")
                    label = f"{device} — " + {
                        "stale": "expired evidence",
                        "ambiguous": "conflicting evidence",
                    }.get(status, "unknown profile; manual setup available")
                options.append({"value": device, "label": label})
            fields[vol.Optional("device")] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        fields[vol.Optional("manual", default=False)] = bool
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"count": str(len(candidates))},
        )

    async def async_step_detected(self, user_input=None):
        """Register exactly the profile reviewed by the user, subject to revalidation.

        @param user_input Chosen name or request to return to discovery.
        """
        if not self._review or (user_input and user_input.get("rescan")):
            return await self.async_step_discover()
        review = self._review
        errors = {}
        if user_input is not None:
            try:
                candidates, ready = await self._discoveries()
                current = self._detected_profile(candidates.get(review["device"], {}))
                if (
                    not ready
                    or not current
                    or any(
                        current[key] != review[key]
                        for key in ("token", "outputs", "suffix")
                    )
                ):
                    self._review = None
                    return self._discovery_form(
                        candidates, {"base": "detection_changed"}
                    )
                await self._client().command(
                    "add",
                    {
                        "device": review["device"],
                        "name": user_input["name"],
                        "auto_detect": True,
                        "profile_token": review["token"],
                    },
                )
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )
            except CommandFailed:
                # A fresh selection is required after rejection or uncertain completion.
                self._review = None
                return self._discovery_form({}, {"base": "detection_failed"})
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except IncompatibleError:
                errors["base"] = "incompatible"
            except LivoloError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="detected",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=f"Livolo {review['device']}"): vol.All(
                        str, vol.Length(min=1, max=100)
                    ),
                    vol.Optional("rescan", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "device": review["device"],
                "outputs": str(review["outputs"]),
                "suffix": review["suffix"],
            },
        )

    async def _action(self, step, action, schema, user_input):
        """Execute one deliberate registry or radio operation.

        @param step Visible form step.
        @param action Native service action.
        @param schema Validated form fields.
        @param user_input User-submitted service arguments.
        """
        errors = {}
        if user_input is not None:
            data = self.config_entry.data
            client = Client(
                async_get_clientsession(self.hass), data[CONF_URL], data[CONF_TOKEN]
            )
            payload = dict(user_input)
            if step == "forget":
                if not payload.pop("confirm", False):
                    errors["base"] = "confirm_required"
                else:
                    errors = {}
            if not errors:
                try:
                    await client.command(action, payload)
                    return self.async_create_entry(
                        title="", data=dict(self.config_entry.options)
                    )
                except CommandFailed as error:
                    errors["base"] = "command_failed"
                    return self.async_show_form(
                        step_id=step,
                        data_schema=schema,
                        errors=errors,
                        description_placeholders={
                            "outcome": str(error.result.get("status", "unknown"))
                        },
                    )
                except LivoloError:
                    errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id=step,
            data_schema=schema,
            errors=errors,
            description_placeholders={"outcome": ""},
        )

    async def async_step_add(self, user_input=None):
        """Register a verified explicit switch profile before opening pairing.

        @param user_input Wire identity and configured profile.
        """
        return await self._action(
            "add",
            "add",
            vol.Schema(
                {
                    vol.Required("device"): str,
                    vol.Required("name"): str,
                    vol.Required("outputs", default=2): vol.In([1, 2]),
                    vol.Required("suffix", default="0002"): str,
                }
            ),
            user_input,
        )

    async def async_step_pair(self, user_input=None):
        """Open or close a targeted pairing window, never automatic admission.

        @param user_input Registered device and bounded duration.
        """
        return await self._action(
            "pair",
            "pair",
            vol.Schema(
                {
                    vol.Required("device"): str,
                    vol.Required("seconds", default=120): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=300)
                    ),
                }
            ),
            user_input,
        )

    async def async_step_refresh(self, user_input=None):
        """Request one bounded read-only state transaction.

        @param user_input Registered device identity.
        """
        return await self._action(
            "refresh", "refresh", vol.Schema({vol.Required("device"): str}), user_input
        )

    async def async_step_forget(self, user_input=None):
        """Forget the host record after explicit confirmation, without factory reset.

        @param user_input Device identity and explicit confirmation.
        """
        return await self._action(
            "forget",
            "remove",
            vol.Schema(
                {
                    vol.Required("device"): str,
                    vol.Required("confirm", default=False): bool,
                }
            ),
            user_input,
        )
