"""Constants for the Vinted Go parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "vinted_go"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping this carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Which optional contract fields this carrier's API actually populates — feeds
# the comparison table on the docs site. Keep in lockstep with
# normalize_parcel() in parcels.py: everything not listed here comes back as a
# literal None there. Vinted Go's account payload carries the service point
# but no sender/receiver, ETA, weight or dimensions.
CAPABILITIES = frozenset({"pickup_point", "url", "history"})

# --- Vinted Go account API (carrier.vintedgo.com/members) --------------------
#
# Vinted Go is account-based: a user logs in once with their e-mail (the same
# passwordless flow the app uses) and the integration then auto-imports every
# parcel on that account — both the ones they SENT and the ones they RECEIVE.
#
# Auth is passwordless e-mail verification (no password, no OAuth/captcha):
#   1. POST registrations       {email}  → Vinted Go e-mails a verification link
#   2. POST registrations/confirm {token} → {session_token, refresh_token}
#   3. GET  shipments            (Bearer session_token) → the parcels
#   4. POST sessions/refresh     {refresh_token} → a fresh pair (no re-login)
#   5. DELETE sessions           {refresh_token} → logout
# The session_token is a 7-day JWT; only the refresh_token is persisted.
API_BASE = "https://carrier.vintedgo.com/members"
REGISTER_URL = f"{API_BASE}/registrations"
CONFIRM_URL = f"{API_BASE}/registrations/confirm"
REFRESH_URL = f"{API_BASE}/sessions/refresh"
LOGOUT_URL = f"{API_BASE}/sessions"
SHIPMENTS_URL = f"{API_BASE}/shipments"
ME_URL = f"{API_BASE}/users/me"

# Per-shipment timeline. This is the **public** (keyless) endpoint — it also
# answers for the account's own ``tracking_code`` values — so we reuse it for
# each shipment's status + history instead of paying for a second auth call.
# 200 → {"tracking_events": [...], "meta": {...}}; 404 → {"error_code":"not_found"}.
TRACKING_EVENTS_URL = f"{API_BASE}/public/v1/tracking_events/{{tracking_code}}"

# Human-facing deep link surfaced on each parcel's ``url`` field (not fetched).
TRACKING_URL = (
    "https://vintedgo.com/en/tracking/{tracking_code}?country=nl&region=europe"
)

# --- Config entry data -------------------------------------------------------
# The account e-mail (display only) and the persisted refresh token; the numeric
# Vinted Go user id is the entry's unique_id so the same account can't be added
# twice.
CONF_EMAIL = "email"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_USER_ID = "user_id"

# Direction tag on each shipment (Vinted Go's ``contact_type``): a parcel the
# user is receiving vs one they sent. Maps to the suite's incoming / outgoing.
CONTACT_TYPE_RECIPIENT = "recipient"
CONTACT_TYPE_SENDER = "sender"

# --- Options -----------------------------------------------------------------
# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes). A user-tunable cadence is a deliberate divergence
# from the HA Core "polling is not configurable" rule — wanted in a HACS parcel
# tracker. Default 30 min keeps the account API gently loaded.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default (a large attribute, and
# it costs one timeline call per parcel).
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
