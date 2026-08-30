"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: :data:`_STATUS_MAP` and
:func:`normalize_parcel`. Everything else — the timestamp parsing, the history
builder, the sort contract, the delivered filter, the one-shot warning for
unmapped statuses — is suite-wide machinery and should be left alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-vinted-go/issues/new"
    "?template=unrecognised_status.yml"
)

# Vinted Go status vocabulary → canonical ParcelStatus.
#
# The keys are the event ``group`` value (snake_case), NOT the camelCase keys of
# the in-page i18n label map: the live payload emits ``group:"in_transit"`` while
# the label map calls it ``inTransit``. We map on ``group`` because it is
# language-independent (the human ``message`` text is not).
#
# A return leg does NOT get its own "return_"-prefixed group — a confirmed
# receiving account's return history reuses the plain "in_transit" /
# "ready_for_pickup" groups with the event's ``state`` field set to
# ``"return"`` instead of ``"delivery"``. Since both directions map to the
# same ParcelStatus here, the ``state`` field is not needed for mapping.
#
# ✓ = confirmed from a live parcel; the rest are inferred from the label map's
# full status universe (the snake_case form of each camelCase label key) and are
# best-effort until a real parcel in that state is seen — an unmapped ``group``
# surfaces as ``unknown`` plus a one-shot warning, which is how the map is
# corrected. See the carrier notes in CLAUDE.md.
_STATUS_MAP: dict[str, ParcelStatus] = {
    # announced / label created
    "created": ParcelStatus.REGISTERED,  # ✓
    "tracking_code_created": ParcelStatus.REGISTERED,
    # handed over / moving through the network
    "shipped": ParcelStatus.IN_TRANSIT,  # ✓
    "hand_over": ParcelStatus.IN_TRANSIT,
    "in_depot": ParcelStatus.IN_TRANSIT,
    "left_depot": ParcelStatus.IN_TRANSIT,
    "in_transit": ParcelStatus.IN_TRANSIT,  # ✓
    "redirected": ParcelStatus.IN_TRANSIT,  # ✓ sent to a different pickup point, still moving
    # on its way to the delivery address
    "in_delivery": ParcelStatus.OUT_FOR_DELIVERY,
    # waiting at a pickup point / locker
    "available_for_pickup": ParcelStatus.AT_PICKUP_POINT,
    "ready_for_pickup": ParcelStatus.AT_PICKUP_POINT,  # ✓
    "ready_for_collection_at_merchant": ParcelStatus.AT_PICKUP_POINT,
    # delivered / concluded
    "delivered": ParcelStatus.DELIVERED,  # ✓ a recipient parcel's terminal event
    "concluded": ParcelStatus.DELIVERED,
    # exceptions
    "disposed": ParcelStatus.PROBLEM,
    "empty_locker_found": ParcelStatus.PROBLEM,
    "lost": ParcelStatus.PROBLEM,
    "pickup_failed": ParcelStatus.PROBLEM,
    "cancelled": ParcelStatus.PROBLEM,
    "pickup_cancelled": ParcelStatus.PROBLEM,
    # returns to sender
    "return": ParcelStatus.RETURNING,  # ✓ return in progress
    "returned": ParcelStatus.DELIVERED,  # ✓ return concluded — handed back to the sender
    "return_return": ParcelStatus.RETURNING,
    "return_to_sender": ParcelStatus.RETURNING,
    "return_in_transit": ParcelStatus.RETURNING,
    "return_ready_for_pickup": ParcelStatus.AT_PICKUP_POINT,
    "return_delivered": ParcelStatus.DELIVERED,
    "return_lost": ParcelStatus.PROBLEM,
    "return_disposed": ParcelStatus.PROBLEM,
    "return_empty_locker_found": ParcelStatus.PROBLEM,
    "return_cancelled": ParcelStatus.PROBLEM,
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()

# We have never seen a populated ``point`` (pickup location) in live data, so we
# log its shape once when a real parcel first carries one — a pre-1.0 "help us
# confirm this" signal. Keys only, never values (a point is an address).
_point_shape_logged = False


def _note_point_shape(point: dict) -> None:
    """One-shot: report the pickup-point structure so a tester can confirm it."""
    global _point_shape_logged
    if _point_shape_logged:
        return
    _point_shape_logged = True
    keys = sorted(point.keys())
    if point.get("name"):
        _LOGGER.warning(
            "Vinted Go pickup point seen for the first time (fields=%s) — we've "
            "not confirmed this shape against real data. Please help us verify it: %s",
            keys,
            NEW_ISSUE_URL,
        )
    else:
        _LOGGER.warning(
            "Vinted Go pickup point present but no 'name' field (fields=%s) — the "
            "point name will be blank. Please report so we can map it: %s",
            keys,
            NEW_ISSUE_URL,
        )


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Vinted Go status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


# Barcodes we've already warned about, so a persistently unrecognised
# contact_type doesn't re-log on every poll.
_unmapped_contact_types_logged: set[str] = set()


def warn_unrecognised_contact_type(barcode: str | None, contact_type: str | None) -> None:
    """Log once when a shipment's ``contact_type`` matches neither known value.

    The coordinator splits shipments into incoming/outgoing purely by
    ``contact_type == "recipient"`` / ``"sender"``. Anything else (missing,
    renamed, a value we've never seen) matches neither branch and the
    shipment would otherwise vanish from every sensor with no trace.
    """
    key = barcode or "unknown"
    if key in _unmapped_contact_types_logged:
        return
    _unmapped_contact_types_logged.add(key)
    _LOGGER.warning(
        "Vinted Go shipment %s has an unrecognised contact_type (%s) — it "
        "matches neither incoming nor outgoing, so it won't appear on any "
        "sensor. Please report it: %s",
        key,
        contact_type,
        NEW_ISSUE_URL,
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. For Vinted Go each event carries a ``group``
    status code (what we map on) and a human ``message`` — so ``raw_status`` is
    the readable ``message``, falling back to the ``group`` code. Sorted oldest →
    newest and capped to the most recent ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("timestamp"))
        if not timestamp:
            continue
        group = event.get("group")
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(group),
            "raw_status": event.get("message") or group,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def latest_event(events: list | None) -> dict | None:
    """Return the most recent event (by timestamp) from a Vinted Go timeline.

    Events arrive oldest → newest, but sort defensively rather than trust the
    order. An event whose timestamp will not parse still counts — it falls to
    the end, so a timeline of only-unparseable events still yields its last
    entry rather than ``None``.
    """
    valid = [event for event in events or [] if isinstance(event, dict)]
    if not valid:
        return None
    return max(
        valid,
        key=lambda event: parse_iso(event.get("timestamp")) or datetime.min.replace(
            tzinfo=timezone.utc
        ),
    )


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key the carrier does not expose is
    ``None`` — never omitted.

    ``raw`` is one account **shipment** (from ``members/shipments``) with its
    ``tracking_events`` merged in (from the public timeline). Field sources:

    * ``status`` ← the latest timeline event's ``group`` (the mapping proven on
      the public endpoint), falling back to the shipment's ``resolution`` when
      the timeline is momentarily unavailable.
    * ``raw_status`` ← the latest event's human ``message`` (falls back to the
      ``group`` / ``shipment_state``).
    * ``pickup`` / ``pickup_point`` ← ``AT_PICKUP_POINT`` and ``point.name`` —
      the account payload *does* carry the service point (unlike the keyless
      endpoint). The locker code (``resolved_pick_up_code``) and item title
      (``content_title``) stay under ``raw``.
    * No sender/receiver name, ETA, weight or dimensions — Vinted Go exposes
      none; kept ``None`` for cross-carrier parity. The direction (sent vs
      received) is the shipment's ``contact_type``, which the coordinator uses
      to split incoming from outgoing — it is not a canonical field.
    """
    tracking_code = raw.get("tracking_code")
    events = raw.get("tracking_events") or []
    current = latest_event(events)
    group = current.get("group") if current else None

    if group is not None:
        status = map_parcel_status(group)
    else:
        status = _fallback_status(raw)
    delivered = status is ParcelStatus.DELIVERED

    if current:
        raw_status = current.get("message") or group
        delivered_at = to_iso_timestamp(current.get("timestamp"))
    else:
        raw_status = raw.get("shipment_state")
        delivered_at = to_iso_timestamp(raw.get("last_tracking_event_at"))

    point = raw.get("point")
    if isinstance(point, dict) and point:
        _note_point_shape(point)
    pickup_point = point.get("name") if isinstance(point, dict) else None

    return {
        "carrier": "Vinted Go",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": pickup_point or None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def _fallback_status(shipment: dict) -> ParcelStatus:
    """Coarse status from the shipment fields when the timeline is unavailable.

    Best-effort: only ``resolution == "delivered"`` is confirmed from live data;
    anything else defers to ``unknown`` (the timeline is the real source, and
    it almost always answers).
    """
    if shipment.get("resolution") == "delivered":
        return ParcelStatus.DELIVERED
    return ParcelStatus.UNKNOWN


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
