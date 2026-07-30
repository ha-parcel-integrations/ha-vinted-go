"""Tests for Vinted Go sensor property logic."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from custom_components.vinted_go.const import ParcelStatus
from custom_components.vinted_go.sensor import (
    VintedGoDeliveredParcelsSensor,
    VintedGoIncomingParcelsSensor,
    VintedGoLastUpdateSensor,
    VintedGoOutgoingDeliveredSensor,
    VintedGoOutgoingParcelsSensor,
    VintedGoParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {"email": "a@b.c"}
    return entry


def _coordinator(
    data=None, delivered=None, outgoing=None, delivered_outgoing=None
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data or []
    coordinator.delivered = delivered or []
    coordinator.outgoing = outgoing or []
    coordinator.delivered_outgoing = delivered_outgoing or []
    return coordinator


def _parcel(barcode: str, status: ParcelStatus = ParcelStatus.IN_TRANSIT) -> dict:
    return {"barcode": barcode, "status": status, "pickup": False}


def test_incoming_summary():
    coordinator = _coordinator(data=[_parcel("A"), _parcel("B")])
    sensor = VintedGoIncomingParcelsSensor(coordinator, _entry(), lambda _: None, set())
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_outgoing_summary():
    coordinator = _coordinator(outgoing=[_parcel("X")])
    sensor = VintedGoOutgoingParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "X"


def test_delivered_summaries():
    coordinator = _coordinator(
        delivered=[_parcel("D", ParcelStatus.DELIVERED)],
        delivered_outgoing=[_parcel("E", ParcelStatus.DELIVERED)],
    )
    assert VintedGoDeliveredParcelsSensor(coordinator, _entry()).native_value == 1
    assert VintedGoOutgoingDeliveredSensor(coordinator, _entry()).native_value == 1


def test_parcel_sensor_finds_incoming_and_outgoing():
    coordinator = _coordinator(
        data=[_parcel("A", ParcelStatus.AT_PICKUP_POINT)],
        outgoing=[_parcel("X", ParcelStatus.OUT_FOR_DELIVERY)],
    )
    incoming = VintedGoParcelSensor(coordinator, _entry(), "A")
    outgoing = VintedGoParcelSensor(coordinator, _entry(), "X")
    assert incoming.native_value == ParcelStatus.AT_PICKUP_POINT
    assert outgoing.native_value == ParcelStatus.OUT_FOR_DELIVERY


def test_parcel_sensor_missing_barcode():
    sensor = VintedGoParcelSensor(_coordinator(data=[_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_last_update_sensor():
    coordinator = _coordinator()
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    coordinator.last_success_time = moment
    sensor = VintedGoLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment
