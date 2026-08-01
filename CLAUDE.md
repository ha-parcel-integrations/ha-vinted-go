# Working in this repository

Home Assistant custom integration for **Vinted Go** parcel tracking. Distributed
via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
Account-based (passwordless e-mail login), no manual services. No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (inferred statuses) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed status/shape |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client) | *Deliberate skill divergences* |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

Vinted Go is the rebranded **Homerr** (acquired Oct 2023, brand folded Mar 2024).
A **PUDO carrier** — parcels are essentially always collected from a service
point, not delivered to the door. **Not** the Vinted *marketplace* API (a
different backend).

### Account model — passwordless e-mail login

An account integration (like InPost/PostNL): the user logs in once, the
coordinator auto-imports every parcel (received and sent). Verified live — no
password, no OAuth, no captcha:

```
POST members/registrations         {email}          → 200; e-mails a verify link
POST members/registrations/confirm {token}          → {session_token, refresh_token}
GET  members/shipments             (Bearer session)  → parcels (both directions)
POST members/sessions/refresh      {refresh_token}   → fresh pair (silent renew)
DELETE members/sessions            {refresh_token}   → logout (on entry removal)
GET  members/users/me              (Bearer)          → {user_id} (entry unique_id)
```

- The config flow accepts the whole verify link **or** the bare token
  (`config_flow.extract_token`).
- **Only the `refresh_token` is persisted** (`entry.data`); `session_token` is a
  7-day JWT in memory. `api._authed_get` refreshes on a 401 (or when it has no
  token) and retries once; a failed refresh raises `VintedGoAuthError` →
  `ConfigEntryAuthFailed` → reauth.
- **Refresh rotates the refresh token** — `VintedGoApiClient` hands the new one
  back via `on_tokens_updated`; `__init__` persists it. Do not drop this, a stale
  token forces a re-login.
- Base host is `carrier.vintedgo.com`. The OAuth strings in the APK are a bundled
  library, **not** the login (confirmed by decompiling).

### Timeline — public keyless endpoint (per shipment)

The shipment carries a coarse state; for a reliable status + history the
coordinator fetches each shipment's timeline from the **public** (no-auth)
endpoint:

```
GET members/public/v1/tracking_events/{tracking_code}
  200 → {"tracking_events":[...], "meta":{...}}   404 → {"error_code":"not_found"}
```

`_enrich` calls it only when `last_tracking_event_at` changes (cached in
`_timeline_cache`), so a steady list costs one shipments call and no timeline
calls.

### Payload → canonical mapping

`normalize_parcel` takes a shipment with its timeline events merged in.
- `status` ← the **latest timeline event's `group`** (`_STATUS_MAP`), falling
  back to `_fallback_status` (the shipment `resolution`) when the timeline is
  momentarily unavailable. **Map on `group`, never `state`** (`state` is
  `"delivery"` on every event).
- `raw_status` ← the latest event's `message` (fallback `group` /
  `shipment_state`). `delivered` ← status is `DELIVERED`; `delivered_at` ← latest
  event's timestamp.
- `pickup` / `pickup_point` ← `AT_PICKUP_POINT` and `point.name` (the **account**
  payload carries the service point, unlike the keyless endpoint). Locker code
  (`resolved_pick_up_code`) and item title (`content_title`) stay under `raw`.
- No sender/receiver/ETA/weight/dimensions — `None`, kept for parity.
  `planned_from`/`planned_to` are **always `None`** (no ETA).
- **Direction** is `contact_type` (`recipient`/`sender`), used by the coordinator
  to split incoming/outgoing; not canonical, lives under `raw`.

### Status vocabulary (pre-1.0)

`_STATUS_MAP` keys are snake_case `group` values. Only `created`, `shipped`,
`in_transit`, `delivered` are **confirmed from live data**; the rest are inferred
(⚠ in the map), best-effort until a real parcel in that state is seen. Unmapped
`group` → `unknown` + one-shot WARNING. `_fallback_status` only trusts
`resolution == "delivered"`; everything else defers to the timeline.

### Events — incoming and outgoing, no delivery-time

Received (`contact_type: recipient`) fire `parcel_registered` / `_status_changed`
/ `_delivered`; sent (`sender`) fire `outgoing_parcel_status_changed` /
`_outgoing_parcel_delivered` (no outgoing `registered`). There is **no**
`*_delivery_time_changed` (no ETA). Each direction has its own first-refresh
suppression via `_known_state` / `_known_outgoing_state`, runs over its
active+delivered set combined, and the hop **to** delivered fires only
`_delivered`. `device_id` on every payload (`_cached_device_id`).

## Options and reloads — account-based model

The options flow is one sectioned form. **Account-based**, so it calls
`async_schedule_reload` on submit and registers **no** update listener (combining
a listener with a reload-on-update flow is deprecated, error in HA 2026.12+). The
user-tunable poll interval is a deliberate HACS divergence (see CONVENTIONS.md).

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (register/confirm/refresh/shipments/timeline, error types, session refresh) | **yes** |
| `const.py` (domain, endpoints, `ParcelStatus`, keys) | partly (endpoints) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, enrich, split, event firing) | mostly not |
| `config_flow.py` (2-step e-mail login, reauth, options) | partly (`extract_token`) |
| `__init__.py` (client setup, refresh-token persistence, first refresh, logout-on-remove) | mostly not |
| `sensor.py` / `button.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |

No services — the account auto-imports parcels. `parcels.py` is free of I/O and
HA objects so the per-carrier part stays unit-testable. Config:
`ConfigEntry.runtime_data` (typed, no `hass.data`), `PARALLEL_UPDATES = 0`,
coordinator takes `config_entry=entry`. The coordinator maps `VintedGoAuthError`
→ `ConfigEntryAuthFailed` and `VintedGoApiError` → `UpdateFailed`; per-parcel
timeline fetches are best-effort (return `None`) so one bad timeline never fails
the poll. Entities: `has_entity_name` + `translation_key`, `icons.json`,
translated units, `_attr_attribution`, `_unrecorded_attributes` on anything with
a parcel list or `raw`. Over-redact diagnostics.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (no-ops elsewhere):
`disable_socket` is neutralised (Windows event loops need AF_INET socketpairs;
the 127.0.0.1 allowlist stays) and HA's `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development is Windows.

## Running tests

```
python -m pytest tests/ --cov=custom_components.vinted_go
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; `docs/api/` is gitignored (local reverse-engineering notes).
