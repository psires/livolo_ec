"""Keep one persistent notice when a previously offered firmware candidate disappears."""

from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

CATALOG_FAILURES = (
    "bundle_missing",
    "bundle_invalid",
    "artifact_missing",
    "artifact_unqualified",
    "incompatible_host",
    "artifact_invalid",
    "signing_key_mismatch",
)


def issue_id(entry):
    """Scope the notice to one HA entry, independently of the radio connection.

    @param entry Config entry for the logical Livolo network.
    """
    return f"{entry.entry_id}_firmware_offer_withdrawn"


def text(value):
    """Keep only bounded public identity strings in persistent issue data.

    @param value Optional version or request identity from the daemon.
    """
    return value[:128] if isinstance(value, str) and value else None


class FirmwareOfferNotice:
    """Remember one observed offer; persist only a notice that actually became necessary."""

    def __init__(self, hass, entry):
        """Bind notice ownership without restoring an obsolete firmware candidate.

        @param hass Home Assistant instance owning the issue registry.
        @param entry Existing network configuration entry.
        """
        self.hass = hass
        self.entry = entry
        self.offer = None

    def clear(self):
        """Forget the observed offer and resolve only this entry's withdrawal notice."""
        self.offer = None
        ir.async_delete_issue(self.hass, DOMAIN, issue_id(self.entry))

    def observe(self, firmware):
        """Handle a validated fresh snapshot without inferring completion or initiating work.

        @param firmware Optional authoritative firmware status mapping.
        """
        if not isinstance(firmware, dict) or not firmware:
            return
        status = firmware.get("status")
        if (
            firmware.get("in_progress") is True
            or firmware.get("recovery")
            or status in ("pending", "unknown")
        ):
            self.clear()
            return
        registry = ir.async_get(self.hass)
        existing = registry.async_get_issue(DOMAIN, issue_id(self.entry))
        previous = self.offer or (existing.data if existing else None)
        confirmed_request = (
            text(firmware.get("request_id")) if status == "confirmed" else None
        )
        if (
            previous
            and confirmed_request
            and confirmed_request != previous.get("confirmed_request")
        ):
            self.clear()
            previous = None
        reason = firmware.get("blocked_reason")
        if reason not in CATALOG_FAILURES:
            if (
                reason != "radio_offline"
                and text(firmware.get("candidate_id"))
                and text(firmware.get("latest_version"))
            ):
                self.clear()
                if (
                    firmware.get("update_available") is True
                    and firmware.get("install_supported") is True
                    and not reason
                ):
                    self.offer = {
                        "offered_version": text(firmware.get("latest_version")),
                        "confirmed_request": confirmed_request,
                    }
            return
        if not previous:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id(self.entry),
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="firmware_offer_withdrawn",
            translation_placeholders={
                "gateway": self.entry.title or "Livolo EC",
                "offered_version": previous.get("offered_version") or "Not reported",
                "installed_version": text(firmware.get("installed_version"))
                or "Not reported",
            },
            data=previous,
        )
