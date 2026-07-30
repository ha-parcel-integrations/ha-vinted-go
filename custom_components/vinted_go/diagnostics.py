"""Diagnostics support for the Vinted Go parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import VintedGoConfigEntry

# Diagnostics are pasted into public issues, so redact anything identifying: the
# account (e-mail, tokens, user id), the parcel numbers, the pickup location and
# code, and the item title. Over-redacting is cheap; under-redacting leaks a
# user's data into a GitHub thread. Event ``message`` text (route cities like
# "Breda, NL") is left in — public route info, not the user's data.
TO_REDACT = {
    # account / session
    "email",
    "refresh_token",
    "session_token",
    "user_id",
    # canonical parcel fields we publish
    "tracking_code",
    "barcode",
    "url",
    # carrier shipment fields
    "content_title",
    "resolved_pick_up_code",
    "point",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VintedGoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Vinted Go config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "incoming_delivered": len(coordinator.delivered or []),
            "outgoing_active": len(coordinator.outgoing or []),
            "outgoing_delivered": len(coordinator.delivered_outgoing or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "incoming_delivered": async_redact_data(
            coordinator.delivered or [], TO_REDACT
        ),
        "outgoing": async_redact_data(coordinator.outgoing or [], TO_REDACT),
        "outgoing_delivered": async_redact_data(
            coordinator.delivered_outgoing or [], TO_REDACT
        ),
    }
