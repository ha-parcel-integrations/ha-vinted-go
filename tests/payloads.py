"""Sample Vinted Go payloads shared by the test modules.

Two shapes:

* a **shipment** — one item of ``GET members/shipments`` (the account list),
  tagged ``contact_type`` (``recipient`` = incoming, ``sender`` = outgoing);
* a **timeline** — ``GET .../tracking_events/{code}`` (the public endpoint),
  ``{"tracking_events": [...], "meta": {...}}``.

The coordinator merges a shipment's timeline events onto the shipment before
normalising, so :func:`parcel_raw` builds that merged dict for the mapping tests.
All values are fake but shaped like the real captured data.
"""
from __future__ import annotations

from custom_components.vinted_go.const import (
    CONTACT_TYPE_RECIPIENT,
    CONTACT_TYPE_SENDER,
)

TOKENS = {"session_token": "sess.eyJ.tok", "refresh_token": "refresh-abc"}
ROTATED_TOKENS = {"session_token": "sess.eyJ.new", "refresh_token": "refresh-def"}


def event(group: str, timestamp: str, message: str) -> dict:
    """One Vinted Go timeline event."""
    return {
        "id": abs(hash((group, timestamp))) % 10**9,
        "message": message,
        "timestamp": timestamp,
        "group": group,
        "state": "delivery",
        "group_header": None,
    }


_BASE_EVENTS = [
    event("created", "2026-07-24T13:06:17.279Z", "Shipment created"),
    event("shipped", "2026-07-25T14:28:58.606Z", "Dropped off at the parcel shop"),
    event("in_transit", "2026-07-29T15:49:45.174Z", "In transit to Breda, NL"),
]
_DELIVERED_EVENT = event("delivered", "2026-07-30T11:02:33.000Z", "Collected")


def timeline(events: list[dict]) -> dict:
    """A public tracking_events response."""
    return {
        "tracking_events": list(events),
        "meta": {"banner": None, "expiration_time": None, "contextual_faq": []},
    }


def shipment(
    code: str,
    contact_type: str,
    *,
    resolution: str | None = None,
    last_at: str = "2026-07-29T15:49:45.174Z",
    point: dict | None = None,
) -> dict:
    """One account shipment (as ``members/shipments`` returns it)."""
    return {
        "tracking_code": code,
        "status_group": "completed" if resolution else "in_progress",
        "contact_type": contact_type,
        "shipment_state": "delivery",
        "resolution": resolution,
        "content_title": "A dress",
        "resolved_pick_up_code": None,
        "pick_up_expires_at": "2026-08-02T09:00:00.000Z",
        "last_tracking_event_at": last_at,
        "point": point,
    }


def parcel_raw(
    code: str,
    contact_type: str = CONTACT_TYPE_RECIPIENT,
    *,
    delivered: bool = False,
    point: dict | None = None,
) -> dict:
    """A shipment with its timeline events merged in (what normalize_parcel sees)."""
    events = list(_BASE_EVENTS)
    if delivered:
        events.append(_DELIVERED_EVENT)
    ship = shipment(
        code,
        contact_type,
        resolution="delivered" if delivered else None,
        last_at=events[-1]["timestamp"],
        point=point,
    )
    return {**ship, "tracking_events": events}


# Ready-made shipment lists for the coordinator tests.
INCOMING_CODE = "VGS0000000000001"
OUTGOING_CODE = "VGS0000000000002"


def shipments_list() -> list[dict]:
    """One active incoming + one active outgoing shipment."""
    return [
        shipment(INCOMING_CODE, CONTACT_TYPE_RECIPIENT),
        shipment(OUTGOING_CODE, CONTACT_TYPE_SENDER),
    ]
