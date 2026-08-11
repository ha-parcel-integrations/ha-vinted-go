"""Tests for the pure parcel-mapping helpers."""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vinted_go.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.vinted_go.parcels import (
    apply_delivered_filter,
    build_history,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import event, parcel_raw

# --- status mapping ---------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("created", ParcelStatus.REGISTERED),
        ("shipped", ParcelStatus.IN_TRANSIT),
        ("in_transit", ParcelStatus.IN_TRANSIT),
        ("in_delivery", ParcelStatus.OUT_FOR_DELIVERY),
        ("available_for_pickup", ParcelStatus.AT_PICKUP_POINT),
        ("delivered", ParcelStatus.DELIVERED),
        ("return_to_sender", ParcelStatus.RETURNING),
        ("lost", ParcelStatus.PROBLEM),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_and_unmapped():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN
    assert map_parcel_status("teleported") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    assert map_event_status(None) is None
    assert map_event_status("something_new") is None
    assert map_event_status("delivered") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("abducted") == ParcelStatus.UNKNOWN
    assert map_parcel_status("abducted") == ParcelStatus.UNKNOWN
    assert caplog.text.count("abducted") == 1
    assert "issues/new" in caplog.text


# --- timestamp helpers ------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_passthrough_and_epoch():
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


# --- build_history ----------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(parcel_raw("VGS1", delivered=True)["tracking_events"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Shipment created"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"group": "in_transit"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_falls_back_to_group_without_message():
    history = build_history([event("in_transit", "2026-04-24T10:00:00Z", "")])
    assert history[0]["raw_status"] == "in_transit"


# --- normalize_parcel -------------------------------------------------------

CANONICAL_KEYS = [
    "carrier", "barcode", "sender", "receiver", "status", "raw_status",
    "delivered", "delivered_at", "planned_from", "planned_to", "pickup",
    "pickup_point", "url", "weight", "dimensions", "history", "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    assert list(normalize_parcel(parcel_raw("VGS1"))) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(parcel_raw("VGS0000000000009", delivered=True))
    assert parcel["carrier"] == "Vinted Go"
    assert parcel["barcode"] == "VGS0000000000009"
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Collected"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-07-30T11:02:33.000Z"
    assert parcel["planned_from"] is None
    assert parcel["url"].endswith("VGS0000000000009?country=nl&region=europe")
    assert parcel["weight"] is None
    assert parcel["history"] is None


def test_normalize_in_transit_parcel():
    parcel = normalize_parcel(parcel_raw("VGS1"))
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["delivered"] is False
    assert parcel["raw_status"] == "In transit to Breda, NL"


def test_normalize_pickup_point_and_bool():
    raw = parcel_raw("VGS1", point={"name": "Vinted Go Point Central Station"})
    raw["tracking_events"].append(
        event("available_for_pickup", "2026-07-30T08:00:00Z", "Ready for pickup")
    )
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Vinted Go Point Central Station"


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(parcel_raw("VGS1", delivered=True), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_fallback_status_without_timeline():
    """When the timeline is unavailable, fall back to the shipment resolution."""
    raw = parcel_raw("VGS1", delivered=True)
    raw["tracking_events"] = []
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["raw_status"] == "delivery"  # shipment_state


def test_normalize_fallback_unknown_without_timeline_or_resolution():
    raw = parcel_raw("VGS1")
    raw["tracking_events"] = []
    raw["resolution"] = None
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.UNKNOWN


def test_normalize_keeps_raw_payload():
    raw = parcel_raw("VGS1")
    assert normalize_parcel(raw)["raw"] is raw


def test_point_shape_logged_once_with_name(caplog):
    import custom_components.vinted_go.parcels as parcels_mod

    parcels_mod._point_shape_logged = False
    raw = parcel_raw("VGS1", point={"name": "Central Station", "address": "x"})
    normalize_parcel(raw)
    normalize_parcel(parcel_raw("VGS2", point={"name": "Other"}))  # one-shot
    assert caplog.text.count("pickup point seen for the first time") == 1
    assert "fields=['address', 'name']" in caplog.text
    assert "issues/new" in caplog.text
    assert "WARNING" in caplog.text


def test_point_without_name_warns(caplog):
    import custom_components.vinted_go.parcels as parcels_mod

    parcels_mod._point_shape_logged = False
    normalize_parcel(parcel_raw("VGS1", point={"label": "Central Station"}))
    assert "no 'name' field" in caplog.text
    assert "fields=['label']" in caplog.text


# --- sort / filter ----------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id="1",
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_are_pickup_point_url_and_history():
    """The account payload carries the service point but no ETA/weight/dimensions."""
    assert CAPABILITIES == {"pickup_point", "url", "history"}
