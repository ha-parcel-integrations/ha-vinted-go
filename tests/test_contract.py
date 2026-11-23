"""The parcel contract's checks, run over every normalised sample."""

import pytest

from custom_components.vinted_go.const import (
    CONTACT_TYPE_RECIPIENT,
    CONTACT_TYPE_SENDER,
    ParcelStatus,
)
from custom_components.vinted_go.parcels import (
    _end_of_day,
    _moment,
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import event, parcel_raw

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

POINT = {"name": "Vinted Go Point Central Station"}


def _with_event(group: str, *, contact_type=CONTACT_TYPE_RECIPIENT, point=POINT):
    raw = parcel_raw("VGS1", contact_type, point=point)
    raw["tracking_events"].append(event(group, "2026-07-30T08:00:00Z", group))
    return raw


def _collected() -> dict:
    raw = _with_event("available_for_pickup")
    raw["tracking_events"].append(event("delivered", "2026-07-31T16:00:00Z", "Taken"))
    return raw


SAMPLES = {
    "active": lambda: parcel_raw("VGS1"),
    "in_transit_to_point": lambda: parcel_raw("VGS1", point=POINT),
    "at_pickup_point": lambda: _with_event("available_for_pickup"),
    "collected": _collected,
    "delivered": lambda: parcel_raw("VGS1", delivered=True),
    "returning": lambda: _with_event("return"),
    "returned": lambda: _with_event("returned"),
    "problem": lambda: _with_event("lost"),
    "outgoing": lambda: _with_event(
        "available_for_pickup", contact_type=CONTACT_TYPE_SENDER
    ),
    "bare": lambda: {"tracking_code": "VGS2"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_contents_is_none_or_one_trimmed_line(parcel):
    contents = parcel["contents"]
    assert contents is None or (
        isinstance(contents, str) and contents == " ".join(contents.split())
    )


def test_delivered_follows_status(parcel):
    # A concluded return is RETURNING yet delivered (back at the sender).
    returned = parcel["status"] is ParcelStatus.RETURNING and parcel["delivered"]
    assert parcel["delivered"] is (
        parcel["status"] is ParcelStatus.DELIVERED or returned
    )
    if parcel["raw_status"] == "Return: Parcel was returned":
        assert parcel["status"] is ParcelStatus.RETURNING
        assert parcel["delivered"] is True
    if parcel["delivered"] and parcel["status"] is not ParcelStatus.DELIVERED:
        assert parcel["raw_status"] is not None


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_received_parcel_with_a_point_is_pickup_before_arrival():
    parcel = normalize_parcel(parcel_raw("VGS1", point=POINT))
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == POINT["name"]


def test_a_received_parcel_without_a_point_is_not_pickup():
    parcel = normalize_parcel(parcel_raw("VGS1"))
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    raw = _collected()
    raw["point"] = None
    parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-07-31T16:00:00Z"
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_a_collected_parcel_keeps_its_point_name():
    parcel = normalize_parcel(_collected())
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == POINT["name"]


def test_a_sent_parcel_never_takes_the_point_as_pickup():
    parcel = normalize_parcel(
        parcel_raw("VGS1", CONTACT_TYPE_SENDER, point={"name": "Drop-off shop"})
    )
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_returning_parcel_keeps_pickup_and_is_not_delivered():
    raw = _with_event("return")
    parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["pickup"] is True


def test_the_helpers_shape_a_moment_and_a_date():
    zone = parse_iso("2026-01-01T00:00:00+01:00").tzinfo
    start, end = planned_window("2099-04-29T13:00:00", None, zone=zone)
    assert end == "2099-04-29T23:59:59+01:00"
    assert (start, end) == ("2099-04-29T13:00:00+01:00", end)
    assert planned_window("2099-04-29", None, zone=zone) == (
        "2099-04-29T00:00:00+01:00",
        "2099-04-29T23:59:59+01:00",
    )
    assert _end_of_day(_moment("2099-04-29", zone)[0], zone).second == 59


def test_the_helpers_empty_and_flag():
    assert planned_for_status(ParcelStatus.DELIVERED, "a", "b") == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, "a", "b") == ("a", "b")
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert not is_pickup(None, ParcelStatus.DELIVERED, [None])
