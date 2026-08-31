"""Sensor platform for the Vinted Go parcel tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import VintedGoConfigEntry
from .const import DOMAIN, ParcelStatus
from .coordinator import VintedGoCoordinator
from .device import ATTRIBUTION, build_device_info

_LOGGER = logging.getLogger(__name__)

# The DataUpdateCoordinator handles fan-out to all entities.
PARALLEL_UPDATES = 0


def _active_parcels(coordinator: VintedGoCoordinator) -> list[dict]:
    """Every active parcel across both directions (incoming + outgoing)."""
    return list(coordinator.data or []) + list(coordinator.outgoing or [])


def _active_barcodes(coordinator: VintedGoCoordinator) -> set[str]:
    """Barcodes of every active parcel (both directions)."""
    return {p.get("barcode", "") for p in _active_parcels(coordinator)}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VintedGoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vinted Go sensor entities from a config entry."""
    coordinator = entry.runtime_data.coordinator
    entry_id = entry.entry_id
    current_barcodes = _active_barcodes(coordinator)

    # Remove per-parcel sensors from the registry whose barcode is no longer
    # active. Scoped to the sensor domain (so it never touches the button) and
    # excluding the summary/diagnostic sensors, whose unique_ids also start with
    # the entry id.
    registry = er.async_get(hass)
    non_parcel_unique_ids = {
        f"{entry_id}_incoming_parcels",
        f"{entry_id}_awaiting_pickup",
        f"{entry_id}_delivered_parcels",
        f"{entry_id}_outgoing_parcels",
        f"{entry_id}_outgoing_delivered_parcels",
        f"{entry_id}_last_update",
    }
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        if (
            entity_entry.domain == "sensor"
            and entity_entry.unique_id.startswith(f"{entry_id}_")
            and entity_entry.unique_id not in non_parcel_unique_ids
        ):
            barcode = entity_entry.unique_id[len(f"{entry_id}_"):]
            if barcode not in current_barcodes:
                registry.async_remove(entity_entry.entity_id)

    entities: list[SensorEntity] = [
        VintedGoIncomingParcelsSensor(
            coordinator, entry, async_add_entities, current_barcodes
        ),
        VintedGoAwaitingPickupSensor(coordinator, entry),
        VintedGoDeliveredParcelsSensor(coordinator, entry),
        VintedGoOutgoingParcelsSensor(coordinator, entry),
        VintedGoOutgoingDeliveredSensor(coordinator, entry),
        VintedGoLastUpdateSensor(coordinator, entry),
    ]
    for parcel in _active_parcels(coordinator):
        entities.append(
            VintedGoParcelSensor(coordinator, entry, parcel.get("barcode", ""))
        )

    async_add_entities(entities)


class _SummarySensor(CoordinatorEntity[VintedGoCoordinator], SensorEntity):
    """Base for the count-of-parcels summary sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = ATTRIBUTION
    _unrecorded_attributes = frozenset({"parcels"})
    _unique_suffix = ""

    def __init__(
        self, coordinator: VintedGoCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{self._unique_suffix}"
        self._attr_device_info = build_device_info(entry)

    def _parcels(self) -> list[dict]:
        raise NotImplementedError

    @property
    def native_value(self) -> int:
        """Return the number of parcels in this list."""
        return len(self._parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the parcel list."""
        return {"parcels": self._parcels()}


class VintedGoIncomingParcelsSensor(_SummarySensor):
    """Active (not-yet-collected) parcels the user is receiving.

    Also spawns a per-parcel sensor for each new barcode (both directions) and
    removes stale ones via the registry (not self-removal, to avoid the ghost
    entity race).
    """

    _attr_translation_key = "incoming_parcels"
    _unique_suffix = "incoming_parcels"

    def __init__(
        self,
        coordinator: VintedGoCoordinator,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
        known_barcodes: set[str] | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._async_add_entities = async_add_entities
        self._known_barcodes: set[str] = known_barcodes or set()

    def _parcels(self) -> list[dict]:
        return list(self.coordinator.data or [])

    def _handle_coordinator_update(self) -> None:
        current_barcodes = _active_barcodes(self.coordinator)

        new_barcodes = current_barcodes - self._known_barcodes
        if new_barcodes:
            self._async_add_entities(
                VintedGoParcelSensor(self.coordinator, self._entry, barcode)
                for barcode in new_barcodes
            )

        removed_barcodes = self._known_barcodes - current_barcodes
        if removed_barcodes:
            registry = er.async_get(self.hass)
            for barcode in removed_barcodes:
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{self._entry.entry_id}_{barcode}"
                )
                if entity_id:
                    registry.async_remove(entity_id)

        self._known_barcodes = current_barcodes
        super()._handle_coordinator_update()


class VintedGoAwaitingPickupSensor(_SummarySensor):
    """Parcels that have arrived at a pickup point and are ready to collect."""

    _attr_translation_key = "awaiting_pickup"
    _unique_suffix = "awaiting_pickup"

    def _parcels(self) -> list[dict]:
        return [
            parcel
            for parcel in (self.coordinator.data or [])
            if parcel.get("pickup")
            and parcel.get("status") == ParcelStatus.AT_PICKUP_POINT
        ]


class VintedGoDeliveredParcelsSensor(_SummarySensor):
    """Recently collected parcels the user received."""

    _attr_translation_key = "delivered_parcels"
    _unique_suffix = "delivered_parcels"

    def _parcels(self) -> list[dict]:
        return list(self.coordinator.delivered or [])


class VintedGoOutgoingParcelsSensor(_SummarySensor):
    """Active parcels the user sent."""

    _attr_translation_key = "outgoing_parcels"
    _unique_suffix = "outgoing_parcels"

    def _parcels(self) -> list[dict]:
        return list(self.coordinator.outgoing or [])


class VintedGoOutgoingDeliveredSensor(_SummarySensor):
    """Recently delivered parcels the user sent."""

    _attr_translation_key = "outgoing_delivered_parcels"
    _unique_suffix = "outgoing_delivered_parcels"

    def _parcels(self) -> list[dict]:
        return list(self.coordinator.delivered_outgoing or [])


class VintedGoParcelSensor(CoordinatorEntity[VintedGoCoordinator], SensorEntity):
    """Per-parcel sensor reporting the status of one Vinted Go parcel."""

    _attr_has_entity_name = True
    _attr_translation_key = "parcel"
    _attr_attribution = ATTRIBUTION
    _unrecorded_attributes = frozenset({"raw", "history"})

    def __init__(
        self, coordinator: VintedGoCoordinator, entry: ConfigEntry, barcode: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._barcode = barcode
        self._attr_unique_id = f"{entry.entry_id}_{barcode}"
        self._attr_translation_placeholders = {"barcode": barcode}
        self._attr_device_info = build_device_info(entry)

    def _get_parcel(self) -> dict[str, Any] | None:
        for parcel in _active_parcels(self.coordinator):
            if parcel.get("barcode") == self._barcode:
                return parcel
        return None

    @property
    def native_value(self) -> str | None:
        """Return the native value of the sensor."""
        parcel = self._get_parcel()
        return parcel.get("status") if parcel else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the extra state attributes."""
        parcel = self._get_parcel()
        return dict(parcel) if parcel else {}


class VintedGoLastUpdateSensor(
    CoordinatorEntity[VintedGoCoordinator], SensorEntity
):
    """Diagnostic sensor reporting when Vinted Go was last polled successfully."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: VintedGoCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_update"
        self._attr_device_info = build_device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        """Return the native value of the sensor."""
        return self.coordinator.last_success_time
