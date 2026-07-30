"""Tests for Vinted Go device triggers."""
from custom_components.vinted_go.const import DOMAIN
from custom_components.vinted_go.device_trigger import (
    TRIGGER_EVENTS,
    async_get_triggers,
)


async def test_get_triggers_returns_incoming_and_outgoing(hass):
    triggers = await async_get_triggers(hass, "device123")
    types = {t["type"] for t in triggers}
    # No *_delivery_time_changed — Vinted Go exposes no ETA. Incoming get
    # registered/status/delivered; outgoing get status/delivered.
    assert types == {
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "outgoing_parcel_status_changed",
        "outgoing_parcel_delivered",
    }
    for trigger in triggers:
        assert trigger["domain"] == DOMAIN
        assert trigger["device_id"] == "device123"


def test_trigger_events_map_to_domain_prefix():
    assert TRIGGER_EVENTS["parcel_registered"] == f"{DOMAIN}_parcel_registered"
