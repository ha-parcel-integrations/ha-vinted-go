"""Tests for Phase 1's dynamic, status-driven polling (dynamic-polling.md
Section 2.2, account-based model).

Pure-function tests for the tiering/scheduling helpers, plus a few
integration checks that ``_async_update_data`` actually wires them up.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vinted_go.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_EMAIL,
    CONF_REFRESH_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    CONTACT_TYPE_RECIPIENT,
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    MID_INTERVAL_MINUTES,
    REFRESH_INTERVAL_AUTO,
    STAGGER_MINUTES,
    ParcelStatus,
)
from custom_components.vinted_go.coordinator import (
    VintedGoCoordinator,
    _hottest_tier_minutes,
    _in_quiet_window,
    _next_anchor,
    _next_update_interval,
    _refresh_interval,
    _refresh_setting,
    _stagger_minutes,
)

from .payloads import event, shipment, timeline

IN = "VGS0000000000001"


def _entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_REFRESH_TOKEN: "rt", CONF_USER_ID: 12345},
        options={
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
            **options,
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


# ---------------------------------------------------------------------------
# Dynamic polling (dynamic-polling.md Section 2.2, account-based) — pure
# helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def test_refresh_interval_reads_minutes_from_options():
    entry = _entry(**{CONF_REFRESH_INTERVAL: 120})
    assert _refresh_interval(entry).total_seconds() == 120 * 60


def test_refresh_interval_starts_hot_when_auto():
    entry = _entry(**{CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO})
    assert _refresh_interval(entry).total_seconds() == HOT_INTERVAL_MINUTES * 60


def test_refresh_setting_passes_through_auto():
    entry = _entry(**{CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO})
    assert _refresh_setting(entry) == REFRESH_INTERVAL_AUTO


def test_quiet_window_is_midnight_to_six():
    assert _in_quiet_window(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    assert _in_quiet_window(datetime(2026, 1, 1, 5, 59, tzinfo=UTC))
    assert not _in_quiet_window(datetime(2026, 1, 1, 6, 0, tzinfo=UTC))
    assert not _in_quiet_window(datetime(2026, 1, 1, 23, 59, tzinfo=UTC))


def test_next_anchor_before_six_is_six_today():
    now = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)
    assert _next_anchor(now) == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def test_next_anchor_after_six_is_midnight_tomorrow():
    now = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    assert _next_anchor(now) == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


def test_stagger_is_stable_and_bounded():
    a = _stagger_minutes("entry-1")
    b = _stagger_minutes("entry-1")
    c = _stagger_minutes("entry-2")
    assert a == b
    assert 0 <= a < STAGGER_MINUTES
    assert 0 <= c < STAGGER_MINUTES


def test_tier_is_mid_when_nothing_active():
    assert (
        _hottest_tier_minutes([], datetime(2026, 1, 1, 12, tzinfo=UTC))
        == MID_INTERVAL_MINUTES
    )


def test_tier_is_mid_for_non_hot_statuses():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [
        {"status": ParcelStatus.REGISTERED, "planned_from": None},
        {"status": ParcelStatus.PROBLEM, "planned_from": None},
        {"status": ParcelStatus.RETURNING, "planned_from": None},
    ]
    assert _hottest_tier_minutes(parcels, now) == MID_INTERVAL_MINUTES


def test_tier_is_hot_when_out_for_delivery_without_planned_from():
    """Vinted Go never populates planned_from at all — every out_for_delivery
    parcel hits this branch, not the lookahead one below."""
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [
        {"status": ParcelStatus.IN_TRANSIT, "planned_from": None},
        {"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": None},
    ]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_hot_when_planned_from_is_unparseable():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [
        {"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": "not-a-date"}
    ]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_hot_within_lookahead_of_planned_from():
    planned = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    now = planned - timedelta(minutes=30)  # inside the 1h lookahead
    parcels = [
        {"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": planned.isoformat()}
    ]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_mid_before_lookahead_of_planned_from():
    planned = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    now = planned - timedelta(hours=3)  # well outside the 1h lookahead
    parcels = [
        {"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": planned.isoformat()}
    ]
    assert _hottest_tier_minutes(parcels, now) == MID_INTERVAL_MINUTES


def test_daytime_candidate_outside_window_is_tier_plus_stagger():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    interval = _next_update_interval(now, MID_INTERVAL_MINUTES, "entry-1")
    stagger = _stagger_minutes("entry-1")
    assert interval == timedelta(minutes=MID_INTERVAL_MINUTES + stagger)


def test_now_inside_quiet_window_jumps_to_next_anchor():
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # an anchor poll itself
    interval = _next_update_interval(now, HOT_INTERVAL_MINUTES, "entry-1")
    assert now + interval == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def test_candidate_landing_in_quiet_window_clamps_to_the_midnight_anchor():
    now = datetime(2026, 1, 1, 23, 50, tzinfo=UTC)
    interval = _next_update_interval(now, MID_INTERVAL_MINUTES, "entry-1")
    assert now + interval == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Dynamic polling — wired into _async_update_data
# ---------------------------------------------------------------------------


async def test_auto_mode_recomputes_interval_and_never_stops(hass):
    """Zero pending parcels must not suspend polling — it's the only discovery path."""
    entry = _entry(**{CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO})
    entry.add_to_hass(hass)
    coord = VintedGoCoordinator(hass, _client([], {}), entry)

    await coord._async_update_data()

    assert coord.current_tier_minutes == MID_INTERVAL_MINUTES
    assert coord.update_interval is not None


async def test_auto_mode_goes_hot_for_out_for_delivery(hass):
    entry = _entry(**{CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO})
    entry.add_to_hass(hass)
    s_in, t_in = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_delivery", "2026-07-29T10:00:00Z")
    coord = VintedGoCoordinator(hass, _client([s_in], {IN: t_in}), entry)

    await coord._async_update_data()

    assert coord.current_tier_minutes == HOT_INTERVAL_MINUTES


async def test_auto_mode_stays_mid_for_in_transit_only(hass):
    entry = _entry(**{CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO})
    entry.add_to_hass(hass)
    s_in, t_in = _ship(IN, CONTACT_TYPE_RECIPIENT, "in_transit", "2026-07-29T10:00:00Z")
    coord = VintedGoCoordinator(hass, _client([s_in], {IN: t_in}), entry)

    await coord._async_update_data()

    assert coord.current_tier_minutes == MID_INTERVAL_MINUTES


async def test_fixed_mode_keeps_configured_interval(hass):
    entry = _entry(**{CONF_REFRESH_INTERVAL: 60})
    entry.add_to_hass(hass)
    coord = VintedGoCoordinator(hass, _client([], {}), entry)

    await coord._async_update_data()

    assert coord.current_tier_minutes is None
    assert coord.update_interval == timedelta(minutes=60)
