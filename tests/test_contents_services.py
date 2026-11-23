"""Tests for the contents action and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.vinted_go.const import (
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_TRACKING_CODE,
    CONF_USER_ID,
    DOMAIN,
)
from custom_components.vinted_go.contents import CONTENTS_STORE_MAX_CODES
from custom_components.vinted_go.coordinator import VintedGoCoordinator

from .payloads import INCOMING_CODE, OUTGOING_CODE, shipments_list, timeline

REFRESH = f"{VintedGoCoordinator.__module__}.VintedGoCoordinator"
UNTRACKED = "VGS0000000000555"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _config_entry(user_id: int = 12345) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: f"user{user_id}@example.test",
            CONF_REFRESH_TOKEN: "rt",
            CONF_USER_ID: user_id,
        },
        unique_id=str(user_id),
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.async_get_shipments = AsyncMock(return_value=shipments_list())
    client.async_get_tracking_events = AsyncMock(return_value=timeline([]))
    client.async_logout = AsyncMock()
    return client


def _patch():
    return patch(
        "custom_components.vinted_go.VintedGoApiClient",
        side_effect=lambda *a, **k: _client(),
    )


async def _setup(hass, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    entry = entry or _config_entry()
    entry.add_to_hass(hass)
    with _patch():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, data: dict) -> None:
    await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
    await hass.async_block_till_done()
    # A refresh requested inside the debouncer's cooldown runs after it.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


async def test_a_parcel_shows_the_carrier_text_without_a_stored_value(hass):
    entry = await _setup(hass)
    assert _contents(entry, INCOMING_CODE) == "A dress"


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass)

    await _call(
        hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": " Running  shoes "}
    )
    assert _contents(entry, INCOMING_CODE) == "Running shoes"

    await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Winter socks"})
    assert _contents(entry, INCOMING_CODE) == "Winter socks"

    await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "  "})
    assert _contents(entry, INCOMING_CODE) == "A dress"


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Running shoes"})

    await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE})

    assert _contents(entry, INCOMING_CODE) == "A dress"


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass)
    await _call(
        hass,
        {CONF_TRACKING_CODE: f" {INCOMING_CODE.lower()} ", "contents": "Shoes"},
    )
    assert _contents(entry, INCOMING_CODE) == "Shoes"


async def test_update_contents_works_for_a_parcel_not_on_the_account_yet(hass):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"})

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )


async def test_update_contents_refreshes_only_when_something_changed(hass):
    await _setup(hass)
    data = {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Running shoes"}
    with patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh:
        await _call(hass, data)
        assert refresh.await_count == 1
        await _call(hass, data)
        assert refresh.await_count == 1
        await _call(hass, {CONF_TRACKING_CODE: UNTRACKED})
        assert refresh.await_count == 1


async def test_an_unchanged_value_writes_nothing(hass, hass_storage):
    entry = await _setup(hass)
    key = STORE_KEY % entry.entry_id
    data = {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Running shoes"}
    await _call(hass, data)
    assert hass_storage[key]["data"] == {"by_code": {INCOMING_CODE: "Running shoes"}}
    del hass_storage[key]

    await _call(hass, data)

    assert key not in hass_storage


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, {CONF_TRACKING_CODE: "  "})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    try:
        with pytest.raises(ServiceValidationError):
            await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Shoes"})
    finally:
        entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_the_store_drops_the_oldest_code_at_201(hass):
    entry = await _setup(hass)
    store = entry.runtime_data.coordinator.contents_store
    for number in range(CONTENTS_STORE_MAX_CODES + 1):
        await _call(hass, {CONF_TRACKING_CODE: f"VGX{number}", "contents": "Item"})

    assert store.get("VGX0") is None
    assert store.get("VGX1") == "Item"
    assert store.get(f"VGX{CONTENTS_STORE_MAX_CODES}") == "Item"


async def test_the_action_goes_with_the_last_loaded_account(hass):
    first = await _setup(hass)
    second = await _setup(hass, _config_entry(67890))

    assert await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, "update_contents")

    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service(DOMAIN, "update_contents")


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Running shoes"})
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    with _patch():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, INCOMING_CODE) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: INCOMING_CODE, "contents": "Running shoes"})
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    with _patch():
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass)
    events = []
    for name in ("registered", "status_changed", "delivered"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)

    await _call(hass, {CONF_TRACKING_CODE: OUTGOING_CODE, "contents": "Running shoes"})

    assert events == []
