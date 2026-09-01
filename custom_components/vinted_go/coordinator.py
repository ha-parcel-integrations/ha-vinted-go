"""Coordinator for the Vinted Go parcel tracker integration.

Fetches the account's shipments (sent + received), enriches each with its public
timeline for a reliable status + history, and publishes the canonical parcel
lists. Event firing lives here too; the parcel mapping is in :mod:`.parcels`.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import VintedGoApiClient, VintedGoApiError, VintedGoAuthError
from .const import (
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    CONTACT_TYPE_RECIPIENT,
    CONTACT_TYPE_SENDER,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    HOT_LOOKAHEAD_HOURS,
    MID_INTERVAL_MINUTES,
    QUIET_WINDOW_END_HOUR,
    QUIET_WINDOW_START_HOUR,
    REFRESH_INTERVAL_AUTO,
    STAGGER_MINUTES,
    ParcelStatus,
)
from .parcels import (
    apply_delivered_filter,
    normalize_parcel,
    sort_parcels_by_ts,
    warn_unrecognised_contact_type,
)

_LOGGER = logging.getLogger(__name__)


def _refresh_setting(entry: ConfigEntry) -> str | int:
    """Return the raw configured refresh setting — ``"auto"`` or a minute count."""
    return entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)


def _refresh_interval(entry: ConfigEntry) -> timedelta:
    """Return the coordinator's *initial* update interval.

    For a fixed setting this is the final word. For ``"auto"`` it is only a
    starting point — the hot cadence, so the first poll after setup happens
    promptly — since ``_async_update_data`` recomputes it every refresh via
    ``_next_update_interval``.
    """
    setting = _refresh_setting(entry)
    if setting == REFRESH_INTERVAL_AUTO:
        return timedelta(minutes=HOT_INTERVAL_MINUTES)
    return timedelta(minutes=int(setting))


def _stagger_minutes(entry_id: str) -> int:
    """Deterministic per-install offset, stable across restarts."""
    digest = hashlib.sha256(entry_id.encode()).hexdigest()
    return int(digest, 16) % STAGGER_MINUTES


def _in_quiet_window(moment: datetime) -> bool:
    """Whether ``moment`` (local time) falls in the no-polling window."""
    return QUIET_WINDOW_START_HOUR <= moment.hour < QUIET_WINDOW_END_HOUR


def _next_anchor(now: datetime) -> datetime:
    """Return the next of the two daily anchors (00:00 / 06:00 local)."""
    six_today = now.replace(
        hour=QUIET_WINDOW_END_HOUR, minute=0, second=0, microsecond=0
    )
    if now < six_today:
        return six_today
    midnight_tomorrow = (now + timedelta(days=1)).replace(
        hour=QUIET_WINDOW_START_HOUR, minute=0, second=0, microsecond=0
    )
    return midnight_tomorrow


def _hottest_tier_minutes(active_parcels: list[dict], now: datetime) -> int:
    """Tier for the account-based model (dynamic-polling.md Section 2.2).

    Unlike a barcode-based coordinator this never returns ``None`` — a single
    account call already returns the full account state, so the mid-tier
    poll is also the only way to discover a new shipment. ``active_parcels``
    is expected to already be incoming + outgoing, not-yet-delivered.
    """
    for parcel in active_parcels:
        if parcel["status"] != ParcelStatus.OUT_FOR_DELIVERY:
            continue
        planned_from = parcel.get("planned_from")
        if not planned_from:
            return HOT_INTERVAL_MINUTES
        planned_dt = dt_util.parse_datetime(planned_from)
        if planned_dt is None:
            return HOT_INTERVAL_MINUTES
        if dt_util.as_utc(now) >= dt_util.as_utc(planned_dt) - timedelta(
            hours=HOT_LOOKAHEAD_HOURS
        ):
            return HOT_INTERVAL_MINUTES

    return MID_INTERVAL_MINUTES


def _next_update_interval(now: datetime, tier_minutes: int, entry_id: str) -> timedelta:
    """Turn a tier into the coordinator's next ``update_interval``.

    Clamp the naive next-due time forward to the next anchor whenever it
    would land inside the quiet window — including when ``now`` itself is
    already inside it (an anchor poll computing its own follow-up).
    """
    if _in_quiet_window(now):
        return _next_anchor(now) - now

    stagger = timedelta(minutes=_stagger_minutes(entry_id))
    candidate = now + timedelta(minutes=tier_minutes) + stagger
    if _in_quiet_window(candidate):
        return _next_anchor(now) - now
    return candidate - now


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
        # Tier last computed by _hottest_tier_minutes when the refresh
        # setting is "auto" — surfaced in diagnostics. None when polling at a
        # fixed interval instead.
        self._current_tier_minutes: int | None = None

    @property
    def current_tier_minutes(self) -> int | None:
        """Tier minutes computed on the last "auto" refresh (diagnostics only)."""
        return self._current_tier_minutes

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
        for p in normalized:
            contact_type = p["raw"].get("contact_type")
            if contact_type not in (CONTACT_TYPE_RECIPIENT, CONTACT_TYPE_SENDER):
                warn_unrecognised_contact_type(p.get("barcode"), contact_type)

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

        setting = _refresh_setting(self.config_entry)
        if setting == REFRESH_INTERVAL_AUTO:
            now = dt_util.now()
            self._current_tier_minutes = _hottest_tier_minutes(
                active_in + self.outgoing, now
            )
            self.update_interval = _next_update_interval(
                now, self._current_tier_minutes, self.config_entry.entry_id
            )
        else:
            self._current_tier_minutes = None
            self.update_interval = timedelta(minutes=int(setting))

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
