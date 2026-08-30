"""Tests for the Vinted Go coordinator: fetch, split, cache and events."""
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vinted_go.api import VintedGoApiError, VintedGoAuthError
from custom_components.vinted_go.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    CONTACT_TYPE_RECIPIENT,
    CONTACT_TYPE_SENDER,
    DOMAIN,
    ParcelStatus,
)
from custom_components.vinted_go.coordinator import VintedGoCoordinator

from .payloads import event, shipment, timeline

IN = "VGS0000000000001"
OUT = "VGS0000000000002"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_REFRESH_TOKEN: "rt", CONF_USER_ID: 12345},
        options={
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
        unique_id="12345",
    )


def _client(shipments, timelines) -> AsyncMock:
    client = AsyncMock()
    client.async_get_shipments.return_value = shipments
    client.async_get_tracking_events.side_effect = lambda code: timelines.get(code)
    return client


def _ship(code, contact_type, group, ts, resolution=None):
    ship = shipment(code, contact_type, resolution=resolution, last_at=ts)
    tl = timeline([event(group, ts, group)])
    return ship, tl


async def test_update_splits_incoming_and_outgoing(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    s_in, t_in = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    s_out, t_out = _ship(OUT, CONTACT_TYPE_SENDER, "in_transit", "2026-07-29T11:00:00Z")
    coord = VintedGoCoordinator(
        hass, _client([s_in, s_out], {IN: t_in, OUT: t_out}), entry
    )

    data = await coord._async_update_data()

    assert [p["barcode"] for p in data] == [IN]
    assert [p["barcode"] for p in coord.outgoing] == [OUT]
    assert coord.delivered == []
    assert coord.delivered_outgoing == []
    assert coord.last_success_time is not None


async def test_unrecognised_contact_type_is_logged_and_dropped_from_both(hass, caplog):
    entry = _entry()
    entry.add_to_hass(hass)
    s_weird, t_weird = _ship(IN, "courier", "in_transit", "2026-07-29T10:00:00Z")
    coord = VintedGoCoordinator(hass, _client([s_weird], {IN: t_weird}), entry)

    data = await coord._async_update_data()

    assert data == []
    assert coord.outgoing == []
    assert IN in caplog.text
    assert "contact_type" in caplog.text


async def test_delivered_goes_to_delivered_lists(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    s_in, t_in = _ship(
        IN, CONTACT_TYPE_RECIPIENT, "delivered", "2026-07-30T10:00:00Z", "delivered"
    )
    coord = VintedGoCoordinator(hass, _client([s_in], {IN: t_in}), entry)

    data = await coord._async_update_data()
    assert data == []
    assert [p["barcode"] for p in coord.delivered] == [IN]


async def test_timeline_is_cached_until_last_event_changes(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    s_in, t_in = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    client = _client([s_in], {IN: t_in})
    coord = VintedGoCoordinator(hass, client, entry)

    await coord._async_update_data()
    await coord._async_update_data()  # same last_tracking_event_at -> cache hit
    assert client.async_get_tracking_events.await_count == 1


async def test_auth_error_becomes_config_entry_auth_failed(hass):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_shipments.side_effect = VintedGoAuthError
    coord = VintedGoCoordinator(hass, client, entry)
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_api_error_becomes_update_failed(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_shipments.side_effect = VintedGoApiError("boom")
    coord = VintedGoCoordinator(hass, client, entry)
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_first_refresh_fires_nothing(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    s_in, t_in = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    coord = VintedGoCoordinator(hass, _client([s_in], {IN: t_in}), entry)

    fired = []
    for suffix in (
        "parcel_registered", "parcel_status_changed", "parcel_delivered",
        "outgoing_parcel_status_changed", "outgoing_parcel_delivered",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", lambda e: fired.append(e))

    await coord._async_update_data()
    await hass.async_block_till_done()
    assert fired == []


async def test_incoming_status_changed_event(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coord = VintedGoCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e))

    s1, t1 = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    client.async_get_shipments.return_value = [s1]
    client.async_get_tracking_events.side_effect = lambda c: {IN: t1}.get(c)
    await coord._async_update_data()  # first refresh: suppressed

    s2, t2 = _ship(IN, CONTACT_TYPE_RECIPIENT, "available_for_pickup", "2026-07-30T08:00:00Z")
    client.async_get_shipments.return_value = [s2]
    client.async_get_tracking_events.side_effect = lambda c: {IN: t2}.get(c)
    await coord._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert events[0].data["new_status"] == ParcelStatus.AT_PICKUP_POINT


async def test_incoming_delivered_event_only(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coord = VintedGoCoordinator(hass, client, entry)

    delivered, changed = [], []
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: delivered.append(e))
    hass.bus.async_listen(f"{DOMAIN}_parcel_status_changed", lambda e: changed.append(e))

    s1, t1 = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    client.async_get_shipments.return_value = [s1]
    client.async_get_tracking_events.side_effect = lambda c: {IN: t1}.get(c)
    await coord._async_update_data()

    s2, t2 = _ship(IN, CONTACT_TYPE_RECIPIENT, "delivered", "2026-07-30T10:00:00Z", "delivered")
    client.async_get_shipments.return_value = [s2]
    client.async_get_tracking_events.side_effect = lambda c: {IN: t2}.get(c)
    await coord._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1
    assert delivered[0].data["barcode"] == IN


async def test_registered_event_for_new_incoming(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coord = VintedGoCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: events.append(e))

    client.async_get_shipments.return_value = []
    client.async_get_tracking_events.side_effect = lambda c: None
    await coord._async_update_data()  # first refresh, empty

    s1, t1 = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    client.async_get_shipments.return_value = [s1]
    client.async_get_tracking_events.side_effect = lambda c: {IN: t1}.get(c)
    await coord._async_update_data()
    await hass.async_block_till_done()

    assert [e.data["barcode"] for e in events] == [IN]


async def test_outgoing_events(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coord = VintedGoCoordinator(hass, client, entry)

    changed, delivered, registered = [], [], []
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_status_changed", lambda e: changed.append(e)
    )
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_delivered", lambda e: delivered.append(e)
    )
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: registered.append(e))

    s1, t1 = _ship(OUT, CONTACT_TYPE_SENDER, "in_transit", "2026-07-29T10:00:00Z")
    client.async_get_shipments.return_value = [s1]
    client.async_get_tracking_events.side_effect = lambda c: {OUT: t1}.get(c)
    await coord._async_update_data()  # first: suppressed, no registered for outgoing

    s2, t2 = _ship(OUT, CONTACT_TYPE_SENDER, "delivered", "2026-07-30T10:00:00Z", "delivered")
    client.async_get_shipments.return_value = [s2]
    client.async_get_tracking_events.side_effect = lambda c: {OUT: t2}.get(c)
    await coord._async_update_data()
    await hass.async_block_till_done()

    assert registered == []  # outgoing never fires registered
    assert changed == []
    assert len(delivered) == 1


async def test_shipment_without_code_is_skipped(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_shipments.return_value = [{"contact_type": "recipient"}]
    client.async_get_tracking_events.side_effect = lambda c: None
    coord = VintedGoCoordinator(hass, client, entry)
    assert await coord._async_update_data() == []
