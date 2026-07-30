# Working in this repository

This is a Home Assistant custom integration for **Vinted Go** parcel
tracking. Distributed via HACS; not part of HA core. It is one carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite and
publishes the same canonical parcel shape, statuses and events as the others,
so the aggregator and cross-carrier dashboards can read every carrier
identically.

It was generated from **ha-carrier-template**. Everything outside the
*Carrier-specific notes* section is suite-wide; when in doubt, check the
template or a sibling repo rather than inventing something new.

## Always consult HA developer documentation

Home Assistant's integration patterns evolve continuously. **Do not rely on
memory of past patterns** — fetch the canonical page before changing a topic
area, and check the developer blog before introducing anything you only "know"
from training data.

| When you change | Fetch first |
|---|---|
| Entity properties, naming, lifecycle, attributes | https://developers.home-assistant.io/docs/core/entity/ |
| Sensor specifics (state/device classes, units) | https://developers.home-assistant.io/docs/core/entity/sensor |
| Config flow, options flow, reauth, reconfigure | https://developers.home-assistant.io/docs/config_entries_config_flow_handler |
| DataUpdateCoordinator pattern | https://developers.home-assistant.io/docs/integration_fetching_data |
| Quality scale rules | https://developers.home-assistant.io/docs/core/integration-quality-scale |
| Diagnostics | https://developers.home-assistant.io/docs/core/integration/diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |

Recent developer-facing changes worth checking before introducing a pattern
from training data:

- https://developers.home-assistant.io/blog — API deprecations, new patterns,
  breaking changes. Recent posts trump older recollection.
- https://github.com/home-assistant/architecture/discussions — design decisions
  in flight that have not reached stable docs yet.

Branding is handled by the local `custom_components/vinted_go/brand/`
folder (HACS reads `icon.png` from it). The official `home-assistant/brands`
repo is for HA Core integrations and does not apply here.

## Carrier-specific notes

Vinted Go is the rebranded **Homerr** (Vinted Go acquired Homerr in Oct 2023,
folded the brand in Mar 2024). It is a **PUDO carrier**: parcels are essentially
always collected from a service point (locker / parcel shop / neighbourhood
point), not delivered to the door. Do not confuse it with the *Vinted
marketplace* API — that is an entirely different backend.

### Account model — passwordless e-mail login

This is an **account integration** (like InPost/PostNL), not a manual tracker.
The user logs in once; the coordinator then auto-imports every parcel on the
account, both received and sent. Auth is **passwordless e-mail** (verified live
against the real API — no password, no OAuth, no captcha):

```
POST members/registrations        {email}          → 200; e-mails a verify link
POST members/registrations/confirm {token}          → {session_token, refresh_token}
GET  members/shipments             (Bearer session)  → the parcels (both directions)
POST members/sessions/refresh      {refresh_token}   → a fresh pair (silent renew)
DELETE members/sessions            {refresh_token}   → logout (on entry removal)
GET  members/users/me              (Bearer)          → {user_id} (the entry unique_id)
```

- The verification link is `https://app.vintedgo.com/auth/verify?token=…`; the
  config flow accepts the whole link **or** the bare token (`config_flow.extract_token`).
- **Only the `refresh_token` is persisted** (in `entry.data`). `session_token`
  is a 7-day JWT held in memory; `api._authed_get` refreshes on a 401 (or when
  it has no token) and retries once. A failed refresh raises `VintedGoAuthError`,
  which the coordinator turns into `ConfigEntryAuthFailed` → reauth.
- **Refresh rotates the refresh token.** `VintedGoApiClient` hands the new one
  back via the `on_tokens_updated` callback; `__init__` persists it to
  `entry.data`. Do not drop this — a stale refresh token means a forced re-login.
- The base API `www` host is `carrier.vintedgo.com`. The OAuth strings in the
  APK are a bundled library, **not** the login (confirmed by decompiling).

### Timeline — the public keyless endpoint (per shipment)

The shipment object carries a coarse state (`status_group` / `shipment_state` /
`resolution`), but for a reliable canonical status + history the coordinator
fetches each shipment's timeline from the **public** endpoint (no auth), which
also answers for the account's own `tracking_code`:

```
GET members/public/v1/tracking_events/{tracking_code}
  200 → {"tracking_events": [...], "meta": {...}}   404 → {"error_code":"not_found"}
```

`_enrich` calls it only when the shipment's `last_tracking_event_at` changes
(cached in `_timeline_cache`), so a steady list costs one shipments call and no
timeline calls.

### Payload → canonical mapping

`normalize_parcel` takes a **shipment** with its timeline events merged in.

- `status` ← the **latest timeline event's `group`** (`parcels._STATUS_MAP`),
  falling back to `_fallback_status` (the shipment `resolution`) when the
  timeline is momentarily unavailable. Map on `group`, never `state` (`state` is
  `"delivery"` on every event).
- `raw_status` ← the latest event's human `message` (falls back to `group` /
  `shipment_state`).
- `delivered` ← status is `DELIVERED`; `delivered_at` ← the latest event's
  timestamp.
- `pickup` / `pickup_point` ← `AT_PICKUP_POINT` and `point.name` — the **account
  payload carries the service point** (unlike the keyless endpoint). The locker
  code (`resolved_pick_up_code`) and item title (`content_title`) stay under
  `raw` (no canonical field for them).
- No sender/receiver name, ETA, weight or dimensions — `None`, kept for parity.
  `planned_from` / `planned_to` are **always `None`** (no ETA).
- **Direction** is the shipment's `contact_type` (`recipient` / `sender`), which
  the **coordinator** uses to split incoming from outgoing. It is not a canonical
  field; it lives under `raw`.

### Status vocabulary

`_STATUS_MAP` keys are the snake_case `group` values. Only `created`, `shipped`,
`in_transit` and `delivered` are **confirmed from live data**; the rest are
inferred (⚠ in the map) and best-effort until a real parcel in that state is
seen. An unmapped `group` → `unknown` + one-shot WARNING with the `issues/new`
link. Likewise the shipment-field fallback (`_fallback_status`) only trusts
`resolution == "delivered"`; everything else defers to the timeline.

### Events — incoming and outgoing, no delivery-time

Received parcels (`contact_type: recipient`) fire
`parcel_registered` / `parcel_status_changed` / `parcel_delivered`; sent parcels
(`sender`) fire `outgoing_parcel_status_changed` / `outgoing_parcel_delivered`
(no `registered`, same as the rest of the suite). There is **no**
`*_delivery_time_changed` — Vinted Go exposes no ETA. Each direction has its own
first-refresh suppression via `_known_state` / `_known_outgoing_state`.

## The canonical parcel contract

Every carrier publishes parcels through `normalize_parcel` in `parcels.py`
with **exactly** these top-level keys, in this order:

`carrier`, `barcode`, `sender`, `receiver`, `status`, `raw_status`,
`delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`,
`pickup_point`, `url`, `weight`, `dimensions`, `history`, `raw`.

- A key the carrier does not expose is `None` — **never omitted**. Consumers
  read the key unconditionally.
- Carrier-specific extras live under `raw`. The aggregator strips `raw`, so
  anything that must survive aggregation has to be top-level.
- `status` is the canonical `ParcelStatus` enum; `raw_status` is the carrier's
  own text. Do not put the carrier's string on `status`.
- **Units**: `weight` in kilograms (float); `dimensions` in centimetres as
  `{length, width, height, text}` where `text` is `"L x W x H cm"` (integers,
  lowercase `x`). Convert before normalising if the carrier reports grams or
  millimetres.
- **Sort contract**: incoming ascending on `planned_from`, delivered descending
  on `delivered_at`, missing timestamps always last (`sort_parcels_by_ts`).
- Summary sensors expose the list under the `parcels` attribute — never
  `shipments`.

`test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards
the key set. Changing it is a suite-wide change: every carrier plus the
aggregator, together.

## Events

Fired on the HA bus by the coordinator, and exposed as no-code device triggers
via `device_trigger.py`:

| Event | When |
|---|---|
| `vinted_go_parcel_registered` | A new received parcel appears |
| `vinted_go_parcel_status_changed` | A received parcel's status changed (`old_status` / `new_status`) |
| `vinted_go_parcel_delivered` | A received parcel reached `delivered` |
| `vinted_go_outgoing_parcel_status_changed` | A sent parcel's status changed |
| `vinted_go_outgoing_parcel_delivered` | A sent parcel reached `delivered` |

Incoming (`contact_type: recipient`) and outgoing (`sender`) each have their own
event set; outgoing has **no `registered`** (same as the rest of the suite).
There is **no** `*_delivery_time_changed` — Vinted Go exposes no ETA.

Rules that are easy to break and must not be:

- **Events are suppressed on the very first refresh** (`_known_state` /
  `_known_outgoing_state` is `None`). Without this, every HA restart floods
  users with "registered" events for parcels that already existed.
- Events run over the **active + delivered set combined** (per direction), so
  the terminal hop is visible in one pass.
- The hop **to** `delivered` fires only the `_delivered` event, never also
  `_status_changed`. A barcode first seen already-delivered fires nothing.
- Every payload is the full normalised parcel plus `device_id` (resolved once
  and cached in `_cached_device_id`). `device_id` is what lets device triggers
  filter per hub.

## Architecture rules

- **`ConfigEntry.runtime_data`** with a typed dataclass; no `hass.data`.
- **The first refresh runs in `__init__.py`, before
  `async_forward_entry_setups`.** Raising `ConfigEntryNotReady` from a
  *forwarded* platform is too late for HA to catch: it logs a warning and
  half-sets-up the entry, and users end up with some platforms and no sensors.
  Never move the first refresh into a platform.
- **`PARALLEL_UPDATES = 0`** in every platform — the coordinator already
  handles fan-out.
- The coordinator takes `config_entry=entry`, so `self.config_entry` works.
- **Auth vs transient errors**: the coordinator maps `VintedGoAuthError` →
  `ConfigEntryAuthFailed` (reauth) and `VintedGoApiError` → `UpdateFailed`
  (retry). Per-parcel **timeline** fetches are best-effort in `api` (return
  `None`), so one bad timeline never fails the whole poll.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove(entity_id)` when a barcode drops out of the
  coordinator data. Self-removal races with coordinator-listener cleanup and
  leaves ghost entities behind.
- **The setup-time stale-entity sweep in `sensor.py` is scoped to
  `entity_entry.domain == "sensor"`** and skips every unique_id in
  `non_parcel_unique_ids`. Without the domain check it deletes the refresh
  button; without the exclusion set it deletes the summary and diagnostic
  sensors. When you add a non-parcel sensor, add its unique_id to that set.
- **`has_entity_name = True` + `translation_key`** on every entity. Names come
  from `strings.json` and the translation files — no `_attr_name`. Icons come
  from `icons.json` — no `_attr_icon`. Units come from
  `entity.sensor.<key>.unit_of_measurement` — no
  `_attr_native_unit_of_measurement`.
- **`_unrecorded_attributes`** on anything carrying a parcel list or a `raw`
  payload, so the recorder's long-term tables stay small.
- `_attr_attribution` on every entity.
- **Unmapped statuses log a one-shot WARNING** per distinct value with a
  copy-paste `issues/new` link; users report them through the *Unrecognised
  parcel status* issue template. That is how the status map grows.
- Diagnostics redact every identifying field — they get pasted into public
  issues. Over-redact rather than under-redact.
- Network calls return raw JSON dicts; there is no DTO layer.

## Options and reloads

The options flow is **one sectioned form** (`data_entry_flow.section`). This is
an **account-based** carrier, so the options flow calls `async_schedule_reload`
on submit and registers **no** update listener. Combining an update listener
with a reload-on-update flow is deprecated today and an error in HA 2026.12+ —
see the [config_entry_listener deprecation](https://developers.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/).

A user-tunable polling interval is a **deliberate divergence** from the HA Core
rule that polling intervals are not configurable: that rule targets core
integrations, and in a HACS parcel tracker a tunable cadence is a wanted
feature. Carriers that throttle or soft-ban unusual traffic are generated with
a fixed cadence instead and have no polling option at all.

## Module layout

| File | Contains | Carrier-specific? |
|---|---|---|
| `api.py` | Account API client (register/confirm/refresh/shipments/timeline), error types, session refresh | **yes** |
| `const.py` | Domain, endpoints, `ParcelStatus`, config + option keys | **partly** (endpoints) |
| `parcels.py` | Status map, `normalize_parcel`, history, sort, filters — pure functions | **partly** (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` | Fetch shipments, enrich w/ timelines, split incoming/outgoing, event firing | mostly not |
| `config_flow.py` | 2-step e-mail login, reauth, options flow | **partly** (`extract_token`) |
| `__init__.py` | Client setup, refresh-token persistence, first refresh, logout-on-remove | mostly not |
| `sensor.py` / `button.py` | Entities | no |
| `device_trigger.py` | Device triggers | no |
| `diagnostics.py` | Redacted diagnostics | **partly** (`TO_REDACT`) |

There are **no services** — the account auto-imports parcels, so there is
nothing to add or remove by hand.

`parcels.py` is deliberately free of I/O and HA objects: the part you rewrite
per carrier stays unit-testable without spinning up Home Assistant.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (both no-ops elsewhere):
pytest-homeassistant-custom-component's `disable_socket` is neutralised
(Windows event loops need AF_INET socketpairs; the connect-time 127.0.0.1
allowlist stays), and HA's hardcoded aiohttp `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development happens on Windows.

## Docs and README

- The README stays **lean and installer-first** (suite house style): no
  per-entity `## Buttons` / `## Calendar` sections; the device-trigger option
  is one sentence folded into **Events**. This file documents everything else.
- **A code change updates the docs in the same commit** where behaviour
  changes — README, this file, and `docs/`.
- `docs/api/` is gitignored: reverse-engineering notes stay local.

## Workflow, commits, releases

See `ha-parcel-integrations/.github/CONVENTIONS.md` for the shared rules
(single-line commit messages, no `v` prefix on tags, semver, maintainer-only
merges, user-facing release notes). Not repeated here.

## Running tests

```
python -m pytest tests/ --cov=custom_components.vinted_go
```

Coverage must stay **above 95%** (the silver `test-coverage` rule). Run before
committing.
