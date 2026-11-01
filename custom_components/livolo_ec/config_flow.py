"""UI configuration, credential repair and bounded network management."""

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

    async def async_step_init(self, user_input=None):
        """Show management actions without changing user names or areas.

        @param user_input Unused menu input.
        """
        return self.async_show_menu(
            step_id="init", menu_options=["add", "pair", "refresh", "forget"]
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
