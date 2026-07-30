# Examples

Ready-to-paste Home Assistant snippets for the Vinted Go integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automations — copy them into your `automations.yaml` or paste them into the Automation editor in **raw editor** mode. |

Vinted Go is account-based: once you log in, your parcels (received and sent) are
imported automatically — there is nothing to add by hand, and no services to
call. The automations here react to the parcel **events** below.

All examples assume a single Vinted Go account. Adjust entity IDs to match yours.

## Events used in the examples

The coordinator fires these on the HA event bus:

| Event | When | Payload |
|---|---|---|
| `vinted_go_parcel_registered` | A new parcel you're receiving appears | The full normalised parcel dict |
| `vinted_go_parcel_status_changed` | A received parcel's status changes | Same, plus `old_status` / `new_status` |
| `vinted_go_parcel_delivered` | A received parcel is delivered | Same (fires *instead of* `status_changed` on that final hop) |
| `vinted_go_outgoing_parcel_status_changed` | A sent parcel's status changes | Same, plus `old_status` / `new_status` |
| `vinted_go_outgoing_parcel_delivered` | A sent parcel is delivered | Same |

Events are suppressed on the first refresh after start-up. There is no
`*_delivery_time_changed` event — Vinted Go exposes no ETA.
