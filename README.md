# Vinted Go Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-vinted-go.svg)](https://github.com/ha-parcel-integrations/ha-vinted-go/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration for your [Vinted Go](https://vintedgo.com) (formerly Homerr) account. Log in once with your e-mail — no password — and it automatically tracks every parcel on your account: the ones you're **receiving** and the ones you **sent**. Vinted Go is a pickup-point carrier across the Benelux and France: parcels are collected from a locker, parcel shop or neighbourhood point.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

> ### ⚠️ Early release — status vocabulary still collecting
>
> Login, parcel auto-import (received and sent), polling and mapping all work.
> What is still incomplete is the **status vocabulary** — Vinted Go's status
> tokens are still being observed from real parcels, so a state we do not map yet
> reports **`unknown`** (never a wrong status) and logs a one-shot warning with a
> ready-made issue link — please
> [report it](https://github.com/ha-parcel-integrations/ha-vinted-go/issues/new?template=unrecognised_status.yml)
> so the mapping can be completed.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Automatic import** of every parcel on your Vinted Go account — no manual tracking numbers
- **Both directions**: parcels you're receiving (incoming) and parcels you sent (outgoing), each with its own sensors
- **Passwordless login**: sign in with your e-mail and a verification link — no password stored
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `at_pickup_point` / `delivered` / …), the carrier's own status text, and a tracking deep-link
- Summary sensors: incoming, outgoing, and recently delivered (both directions)
- Events + device triggers for no-code automations (parcel registered / status changed / delivered, incoming and outgoing)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.7 or newer
- A Vinted Go account (the app's e-mail login — no password needed). If you buy or sell on Vinted in the Benelux/France, your parcels are handled by Vinted Go.

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-vinted-go` as an **Integration**.
3. Install **Vinted Go** and restart Home Assistant.

### Manual

Copy `custom_components/vinted_go` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Vinted Go**, then:

1. **Enter your e-mail address.** Vinted Go sends a verification link to that inbox.
2. **Open the e-mail and paste the verification link** (or just its token) into the next field.

That's it — no password. Your parcels are imported automatically and refreshed on a schedule. The session renews itself silently; you only log in again if Home Assistant asks you to (a rare **reauth** prompt).

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensors. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |
| Polling | Refresh every | 30 min | How often Vinted Go is checked. Slower is gentler on their API. |

## Removal

Standard HA removal applies: **Settings → Devices & Services → Vinted Go → ⋮ → Delete**. The integration revokes its session with Vinted Go when removed.

## Sensors

| Entity | Description |
|---|---|
| `sensor.vinted_go_<email>_incoming_parcels` | Active parcels you're receiving; full list under the `parcels` attribute |
| `sensor.vinted_go_<email>_outgoing_parcels` | Active parcels you sent |
| `sensor.vinted_go_<email>_delivered_parcels` | Recently received parcels (see the retention option) |
| `sensor.vinted_go_<email>_delivered_outgoing_parcels` | Recently delivered parcels you sent |
| `sensor.vinted_go_<email>_parcel_<code>` | One per active parcel (either direction); state is the canonical status, attributes carry the full normalised parcel |
| `sensor.vinted_go_<email>_last_successful_update` | Diagnostic: when Vinted Go was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the matching delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced / label created |
| `in_transit` | In the sorting network |
| `out_for_delivery` | On its way to the delivery address |
| `at_pickup_point` | Waiting for you at a locker, parcel shop or neighbourhood point |
| `delivered` | Collected / delivered |
| `returning` | Going back to the sender |
| `problem` | Vinted Go reports an exception |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`. Vinted Go exposes no estimated delivery time, so parcels have no delivery window.

## Events

The integration fires these on the event bus (also available as device triggers on the Vinted Go device):

| Event | When |
|---|---|
| `vinted_go_parcel_registered` | A new parcel you're receiving appears |
| `vinted_go_parcel_status_changed` | A received parcel's status changes (`old_status` / `new_status`), except the final hop to delivered |
| `vinted_go_parcel_delivered` | A received parcel is delivered |
| `vinted_go_outgoing_parcel_status_changed` | A sent parcel's status changes |
| `vinted_go_outgoing_parcel_delivered` | A sent parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up. (There is no delivery-time event — Vinted Go exposes no ETA.)

## Examples

Ready-to-paste automations live in [`examples/`](examples/), including notifying when a parcel is ready to collect.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.vinted_go: debug
```

## Troubleshooting

- **Home Assistant asks me to reconnect Vinted Go** — the login session could not be renewed (e.g. it was revoked). Follow the reauth prompt: enter your e-mail and paste the fresh verification link.
- **A parcel shows `unknown`** — Vinted Go has not scanned it yet, or reports a status we do not map. If a status logs "Unrecognised Vinted Go status", please [open an issue](https://github.com/ha-parcel-integrations/ha-vinted-go/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of parcel-carrier integrations that all publish the same canonical parcel format, statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same account API as the Vinted Go app, with your own account. It is not affiliated with, endorsed by, or supported by Vinted Go. Be gentle with the polling interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

[MIT](LICENSE)
