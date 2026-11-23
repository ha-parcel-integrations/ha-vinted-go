"""Services for the Vinted Go parcel tracker integration.

`vinted_go.update_contents` sets or clears the text describing what is in
a parcel. The account lists its own parcels, so there is nothing to track by
hand — this is the only service.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import CONF_CONTENTS, CONF_TRACKING_CODE, DOMAIN
from .contents import normalize_code

SERVICE_UPDATE_CONTENTS = "update_contents"

_UPDATE_CONTENTS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_CONTENTS): cv.string,
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Vinted Go services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_UPDATE_CONTENTS):
        return

    async def _update_contents(call: ServiceCall) -> None:
        tracking_code = normalize_code(call.data[CONF_TRACKING_CODE])
        if not tracking_code:
            raise ServiceValidationError("A tracking code is required")
        entry = next(
            (
                candidate
                for candidate in hass.config_entries.async_entries(DOMAIN)
                if candidate.state is ConfigEntryState.LOADED
            ),
            None,
        )
        if entry is None:
            raise ServiceValidationError("Vinted Go is not set up")
        coordinator = entry.runtime_data.coordinator
        if coordinator.contents_store.async_set(
            tracking_code, call.data.get(CONF_CONTENTS)
        ):
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CONTENTS,
        _update_contents,
        schema=_UPDATE_CONTENTS_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the Vinted Go services once no account is loaded."""
    if hass.services.has_service(DOMAIN, SERVICE_UPDATE_CONTENTS):
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_CONTENTS)
