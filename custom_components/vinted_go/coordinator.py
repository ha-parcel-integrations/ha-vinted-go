"""Coordinator for the Vinted Go parcel tracker integration.

Fetches the account's shipments (sent + received), enriches each with its public
timeline for a reliable status + history, and publishes the canonical parcel
lists. Event firing lives here too; the parcel mapping is in :mod:`.parcels`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VintedGoApiClient, VintedGoApiError, VintedGoAuthError
from .const import (
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    CONTACT_TYPE_RECIPIENT,
    CONTACT_TYPE_SENDER,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    ParcelStatus,
)
from .parcels import apply_delivered_filter, normalize_parcel, sort_parcels_by_ts

_LOGGER = logging.getLogger(__name__)


def _refresh_interval(entry: ConfigEntry) -> timedelta:
    """Return the configured refresh interval as a ``timedelta``."""
    minutes = int(entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL))
    return timedelta(minutes=minutes)


class VintedGoCoordinator(DataUpdateCoordinator[list[dict]]):
    """Polls the account and publishes incoming/outgoing parcel lists.

    ``coordinator.data`` is the active **incoming** (received) parcels;
    ``self.delivered`` the delivered incoming ones; ``self.outgoing`` /
    ``self.delivered_outgoing`` the sent ones. The split is Vinted Go's
    ``contact_type`` (``recipient`` → incoming, ``sender`` → outgoing).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: VintedGoApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=_refresh_interval(entry),
        )
        self._client = client
        self.delivered: list[dict] = []
        self.outgoing: list[dict] = []
        self.delivered_outgoing: list[dict] = []
        # tracking_code -> (last_tracking_event_at, timeline dict). The timeline
        # is only refetched when the shipment's last event time changes, so a
        # steady list costs one shipments call and no timeline calls.
        self._timeline_cache: dict[str, tuple[str | None, dict | None]] = {}
        # barcode -> last seen ParcelStatus, per direction. ``None`` on the first
        # refresh so events are suppressed for parcels that already existed when
        # the integration started.
        self._known_state: dict[str, ParcelStatus] | None = None
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        self._cached_device_id: str | None = None
        self.last_success_time: datetime | None = None

    def _device_id(self) -> str | None:
        """Resolve (and cache) this entry's device id for event payloads."""
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(
                dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            ),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(
                CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
            )
        )

    async def _async_update_data(self) -> list[dict]:
        """Fetch the account's shipments, enrich, split and publish."""
        try:
            shipments = await self._client.async_get_shipments()
        except VintedGoAuthError as err:
            raise ConfigEntryAuthFailed("Vinted Go session expired") from err
        except VintedGoApiError as err:
            raise UpdateFailed(str(err)) from err

        # Enrich each shipment with its timeline (cached by last event time).
        codes_seen: set[str] = set()
        enriched: list[dict] = []
        for shipment in shipments:
            code = shipment.get("tracking_code")
            if not code:
                continue
            codes_seen.add(code)
            enriched.append(await self._enrich(shipment, code))
        # Drop cache entries for shipments no longer on the account.
        self._timeline_cache = {
            code: value
            for code, value in self._timeline_cache.items()
            if code in codes_seen
        }

        include_history = self._include_history
        normalized = [
            normalize_parcel(raw, include_history=include_history) for raw in enriched
        ]

        incoming = [
            p for p in normalized
            if p["raw"].get("contact_type") == CONTACT_TYPE_RECIPIENT
        ]
        outgoing = [
            p for p in normalized
            if p["raw"].get("contact_type") == CONTACT_TYPE_SENDER
        ]

        self.delivered, active_in = self._split_delivered(incoming, "planned_from")
        self.delivered_outgoing, active_out = self._split_delivered(
            outgoing, "planned_from"
        )
        self.outgoing = active_out

        # Events run over the active + delivered set combined, so the terminal
        # hop to delivered is visible in one pass.
        combined_in = active_in + self.delivered
        combined_out = active_out + self.delivered_outgoing
        self._fire_change_events(combined_in)
        self._fire_outgoing_change_events(combined_out)
        self._known_state = self._status_map(combined_in)
        self._known_outgoing_state = self._status_map(combined_out)

        self.last_success_time = datetime.now(timezone.utc)
        return active_in

    async def _enrich(self, shipment: dict, code: str) -> dict:
        """Attach the shipment's timeline events, using the cache when fresh."""
        last_at = shipment.get("last_tracking_event_at")
        cached = self._timeline_cache.get(code)
        if cached is not None and cached[0] == last_at:
            timeline = cached[1]
        else:
            timeline = await self._client.async_get_tracking_events(code)
            self._timeline_cache[code] = (last_at, timeline)
        events = timeline.get("tracking_events") if isinstance(timeline, dict) else None
        return {**shipment, "tracking_events": events or []}

    def _split_delivered(
        self, parcels: list[dict], sort_field: str
    ) -> tuple[list[dict], list[dict]]:
        """Return (delivered, active) — delivered filtered + sorted, active sorted."""
        delivered = sort_parcels_by_ts(
            [p for p in parcels if p["delivered"]], "delivered_at", descending=True
        )
        delivered = apply_delivered_filter(delivered, self.config_entry)
        active = sort_parcels_by_ts(
            [p for p in parcels if not p["delivered"]], sort_field
        )
        return delivered, active

    @staticmethod
    def _status_map(parcels: list[dict]) -> dict[str, ParcelStatus]:
        """Map barcode -> status, for the next poll's change detection."""
        return {p["barcode"]: p["status"] for p in parcels if p.get("barcode")}

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire incoming registered / status-changed / delivered events.

        Silent on the very first refresh. There is no
        ``parcel_delivery_time_changed`` — Vinted Go exposes no ETA. The hop
        **to** ``delivered`` fires only ``_parcel_delivered``; a barcode first
        seen already-delivered fires nothing; ``registered`` fires only for a
        new, not-yet-delivered barcode.
        """
        if self._known_state is None:
            return
        device_id = self._device_id()
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                if new_status != ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_registered",
                        {**parcel, "device_id": device_id},
                    )
                continue
            if self._known_state[barcode] != new_status:
                if new_status == ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_delivered",
                        {**parcel, "device_id": device_id},
                    )
                else:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_status_changed",
                        {
                            **parcel,
                            "device_id": device_id,
                            "old_status": self._known_state[barcode],
                            "new_status": new_status,
                        },
                    )

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire outgoing status-changed / delivered events.

        No ``registered`` and no delivery-time event for outgoing (deliberate,
        same as the rest of the suite). The hop to ``delivered`` fires only
        ``_outgoing_parcel_delivered``.
        """
        if self._known_outgoing_state is None:
            return
        device_id = self._device_id()
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            new_status = parcel["status"]
            if self._known_outgoing_state[barcode] == new_status:
                continue
            if new_status == ParcelStatus.DELIVERED:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_delivered",
                    {**parcel, "device_id": device_id},
                )
            else:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": self._known_outgoing_state[barcode],
                        "new_status": new_status,
                    },
                )
