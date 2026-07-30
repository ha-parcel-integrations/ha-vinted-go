"""Tests for the Vinted Go setup / unload / reauth lifecycle."""
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vinted_go.api import VintedGoApiError, VintedGoAuthError
from custom_components.vinted_go.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

from .payloads import shipments_list, timeline


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


def _client(shipments=None) -> MagicMock:
    client = MagicMock()
    client.async_get_shipments = AsyncMock(return_value=shipments or [])
    client.async_get_tracking_events = AsyncMock(return_value=timeline([]))
    client.async_logout = AsyncMock()
    client.refresh_token = "rt"
    return client


def _patch(client):
    return patch("custom_components.vinted_go.VintedGoApiClient", return_value=client)


async def test_setup_and_unload(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    with _patch(_client(shipments_list())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        # summary + diagnostic sensors exist
        assert hass.states.get("sensor.vinted_go_a_b_c_incoming_parcels") is not None
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_failed_starts_reauth(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = _client()
    client.async_get_shipments.side_effect = VintedGoAuthError
    with _patch(client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_missing_refresh_token_triggers_reauth(hass):
    """A pre-account-model entry (no refresh token) goes to reauth, not a crash."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_USER_ID: 12345},  # no refresh_token
        unique_id="12345",
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_setup_not_ready_on_api_error(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = _client()
    client.async_get_shipments.side_effect = VintedGoApiError("down")
    with _patch(client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_per_parcel_sensors_spawn_and_removed(hass):
    """A per-parcel sensor appears for a new parcel and is removed when it goes."""
    from .payloads import event, shipment, timeline

    entry = _entry()
    entry.add_to_hass(hass)
    client = _client([shipment("VGS_A", "recipient", last_at="2026-07-29T10:00:00Z")])
    client.async_get_tracking_events.return_value = timeline(
        [event("in_transit", "2026-07-29T10:00:00Z", "moving")]
    )
    with _patch(client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.vinted_go_a_b_c_parcel_vgs_a") is not None

        # The parcel leaves the account -> its per-parcel sensor is removed.
        client.async_get_shipments.return_value = []
        coordinator = entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.vinted_go_a_b_c_parcel_vgs_a") is None

        # A new parcel arrives -> a per-parcel sensor is spawned.
        client.async_get_shipments.return_value = [
            shipment("VGS_B", "sender", last_at="2026-07-29T12:00:00Z")
        ]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.vinted_go_a_b_c_parcel_vgs_b") is not None


async def test_remove_revokes_session(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = _client()
    with _patch(client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_remove(entry.entry_id)
    client.async_logout.assert_awaited()
