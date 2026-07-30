"""Vinted Go parcel tracker custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VintedGoApiClient
from .const import CONF_REFRESH_TOKEN, PLATFORMS
from .coordinator import VintedGoCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class VintedGoData:
    """Runtime data attached to the Vinted Go config entry."""

    client: VintedGoApiClient
    coordinator: VintedGoCoordinator


type VintedGoConfigEntry = ConfigEntry[VintedGoData]


async def async_setup_entry(hass: HomeAssistant, entry: VintedGoConfigEntry) -> bool:
    """Set up Vinted Go from a config entry."""

    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        # A pre-account-model entry (or a corrupted one) has no refresh token —
        # send the user through reauth to sign in again rather than crash-loop.
        raise ConfigEntryAuthFailed("No Vinted Go refresh token stored")

    @callback
    def _persist_refresh_token(new_refresh_token: str) -> None:
        """Persist a rotated refresh token so it survives restarts."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: new_refresh_token}
        )

    client = VintedGoApiClient(
        async_get_clientsession(hass),
        refresh_token=refresh_token,
        on_tokens_updated=_persist_refresh_token,
    )
    coordinator = VintedGoCoordinator(hass, client, entry)

    # First refresh here, before forwarding to platforms: a failing fetch fails
    # the whole entry cleanly (ConfigEntryNotReady → backoff), and an expired
    # session raises ConfigEntryAuthFailed → reauth. Doing it in a forwarded
    # platform would be too late for HA to catch.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = VintedGoData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VintedGoConfigEntry) -> bool:
    """Unload the Vinted Go config entry (does not revoke the session)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: VintedGoConfigEntry) -> None:
    """Revoke the Vinted Go session when the integration is removed."""
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        return
    client = VintedGoApiClient(async_get_clientsession(hass), refresh_token)
    await client.async_logout()
