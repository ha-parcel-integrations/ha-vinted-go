"""The device every entity of this integration belongs to.

One place, because the sensors and the button must all land on the *same*
device entry — and because the account-based variant only has to change this
file to name devices per account.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_EMAIL, DOMAIN

CONFIGURATION_URL = "https://vintedgo.com"

ATTRIBUTION = "Data provided by Vinted Go"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this account."""
    email = entry.data.get(CONF_EMAIL)
    name = f"Vinted Go ({email})" if email else "Vinted Go"
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer="Vinted Go",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )
