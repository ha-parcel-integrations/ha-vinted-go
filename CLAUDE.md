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
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (inferred statuses) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed status |
| consider "fixing" a lint/pattern the skill flags (inline client) | *Deliberate skill divergences* |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**API mechanics live in `carrier-research/vinted-go/api/` (private research repo)** — the passwordless
e-mail login flow, the shipments + public timeline endpoints, the payload→canonical
mapping and the status vocabulary. Do not duplicate them here.

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
Where this repo diverges from it, that is recorded below under
*Divergences from the scaffold*.

**Polling cadence is not configurable — don't add the option back.** The
Section 2.2 account-based algorithm always runs: `_async_update_data` recomputes
`update_interval` at the end of every refresh (quiet window 00:00–06:00 with two
anchors, hot 15 min / mid 45 min, never a full stop because the mid-tier poll is
also how a new shipment on the account gets discovered, plus a per-install
stagger). The `refresh_interval` dropdown (Phase 1, 1.1.0) is gone; a stale
stored value is never read. The options flow has two sections left, `delivered`
and `history`.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific decisions (integration only)

Vinted Go is the rebranded **Homerr** — a PUDO carrier (parcels collected from a
service point). **Not** the Vinted *marketplace* API (a different backend). An
account integration (like InPost/PostNL): the user logs in once, the coordinator
auto-imports every parcel (received and sent).

- **Token handling (do not weaken).** Only the `refresh_token` is persisted; the
  session token lives in memory and is refreshed on a 401. A **failed** refresh →
  `VintedGoAuthError` → `ConfigEntryAuthFailed` → reauth. **Refresh rotates the
  refresh token** — the client hands the new one back and `__init__` persists it;
  drop that and a stale token forces a re-login.
- **Timeline enrichment is cost-controlled** — the per-shipment timeline is
  fetched only when the shipment's last-event marker changes (cached), so a steady
  list costs one shipments call and no timeline calls. Per-parcel timeline fetches
  are best-effort (return `None`) so one bad timeline never fails the poll.
- **No ETA** — `planned_from`/`planned_to` always `None`, so the calendar and
  `next_delivery` sensor stay inert (kept for parity). Reflected in
  `const.py`'s `CAPABILITIES` (feeds the docs site's comparison table) — keep
  the two in agreement if that ever changes. **Direction**
  (received/sent) splits incoming from outgoing; it lives under `raw`, not a
  canonical field. Unmapped status → `unknown` + one-shot warning.
- **Pickup-point shape confirmed** (issue #8): a real parcel's `point` carries
  `address`, `city`, `code`, `country_code`, `latitude`, `longitude`, `name`,
  `on_vacation_until`, `opening_hours`, `postal_code`. `normalize_parcel` only
  reads `name`; the one-shot warning now fires solely when a populated `point`
  lacks it (an actual gap, not an unconfirmed shape).

## Divergences from the scaffold

Everything not listed here follows the scaffold exactly.

*Options and reloads* — account-based, so `async_schedule_reload` on submit
with **no** update listener. That is the whole divergence now: the interval
half is gone. `CONF_REFRESH_INTERVAL` (15/30/60/120/240 min plus `"auto"`,
Phase 1, 1.1.0) was removed in the Phase 2 convergence (maintainer decision
2026-09-12, every remaining Phase-1 carrier goes unconditional), so this
carrier matches the scaffold's no-interval model — see the tripwire above;
don't add the option back.

*Dynamic polling* — **Vinted Go's shipment payload carries no ETA at all** (see
"No ETA" above), so `planned_from` is always `None`: every `out_for_delivery`
parcel takes the "no `planned_from`" branch straight to the hot tier, and the
1h-lookahead branch is architecturally unreachable from real data — the same
situation `ha-quickpac`/`ha-sameday`/`ha-sunyou`/`ha-ppl-cz` hit. Its tests
therefore exercise that branch with hand-built dicts / a patched tier helper
rather than an invented ETA payload. The tier is surfaced in diagnostics under
`"polling"` (`current_tier_minutes`, `update_interval_seconds`), recomputed at
the end of every `_async_update_data`.

## Running tests

```
python -m pytest tests/ --cov=custom_components.vinted_go
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference now lives in the private `carrier-research/vinted-go/api/`,
not in this repo.
