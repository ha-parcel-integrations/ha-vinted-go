# Vinted Go — still to do

An **account-based** integration (0.9.0, for testers). A user logs in once with
their e-mail (passwordless) and every parcel on the account — received and sent —
is imported automatically. Full test suite passes (86 tests, ~97% coverage),
ruff clean.

## Done

- [x] Passwordless e-mail login (`registrations` → verify link → `confirm` →
      tokens), 2-step config flow, reauth, `extract_token` (link or bare token).
- [x] Session management: 7-day JWT + rotating refresh token, silent refresh on
      401, `on_tokens_updated` persistence, logout on entry removal.
- [x] Coordinator: fetch `members/shipments`, split `contact_type`
      (`recipient` → incoming / `sender` → outgoing), enrich each with its public
      timeline (cached on `last_tracking_event_at`) for status + history.
- [x] Sensors: incoming / outgoing / delivered (both directions) + per-parcel
      (both directions) + last-update; device named per e-mail.
- [x] Events: incoming registered/status/delivered + outgoing status/delivered;
      device triggers for all five.
- [x] Diagnostics redact tokens / e-mail / user id / pickup point / item title.
- [x] Removed the old keyless/manual-tracking model and the track_parcel services.
- [x] README / CLAUDE.md / examples updated.

## Needs a real tester (data confirmation)

Validated live against a real account, but only with **delivered `sender`**
parcels. Confirm with active and received parcels:

- [ ] The **status vocabulary** — only `created`/`shipped`/`in_transit`/
      `delivered` groups are confirmed; the rest of `_STATUS_MAP` (⚠) is inferred.
      Watch for `unknown` + the one-shot WARNING and extend the map.
- [ ] The **`point` shape** on an at-pickup parcel (name/address/hours) so
      `pickup_point` and diagnostics redaction stay right.
- [ ] That **received (`recipient`) parcels** show up and map correctly (the
      account tested had only sent parcels).
- [ ] The account `shipment_state` / `status_group` / `resolution` values for
      active states (currently only used as a fallback when the timeline misses).

## Suite wiring / release

- [ ] Add `vinted_go` to the aggregator's `KNOWN_CARRIERS` and
      `CARRIER_EVENT_PREFIXES` — five events (incoming ×3 + outgoing ×2).
- [ ] Create the `ha-vinted-go` GitHub repo under `ha-parcel-integrations`,
      push, and cut the `0.9.0` release for testers.

Full reverse-engineering (endpoints, DTOs, auth) is in
`carrier-research/vinted-go.md`.

Delete this file once the tester items are resolved.
