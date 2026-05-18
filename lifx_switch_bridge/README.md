# LIFX Switch Bridge

A Home Assistant add-on that exposes LIFX Switch relays and backlights to HA via the [Photons Interactor](https://photons.delfick.com/apps/interactor/) HTTP API and MQTT discovery.

## Why

The native HA LIFX integration does not support LIFX Switch devices. HomeKit Controller is the documented workaround but breaks if the switch's Matter/HomeKit firmware is in a bad state. This bridge uses the local LAN protocol (via Photons Interactor) to:

- Expose each relay as an HA `switch` entity.
- Expose the on-state and off-state backlight colours as HA `light` entities (with proper colour picker).
- Expose device label, haptic duration, and diagnostics as configurable/diagnostic entities.

## Prerequisites

1. **Photons Interactor add-on** running and reachable on the local network (typically at `http://homeassistant.local:6100`).
2. **Mosquitto broker add-on** installed and started; the MQTT integration configured in HA.
3. All LIFX switches configured with **non-LIFX wiring** (so relays directly map to button targets).

## Install

1. Copy this directory to `/addons/lifx_switch_bridge/` on your HA host (via Samba, File Editor, or SSH).
2. Settings -> Add-ons -> Add-on Store -> three-dot menu -> Check for updates.
3. Find "LIFX Switch Bridge" under "Local add-ons", install, start.
4. (Optional) Configure via the add-on Configuration tab if `interactor_url` or other options differ from defaults.

## What you get per switch

- 4x `switch` entities (one per relay).
- 2x `light` entities: backlight on-state colour and off-state colour.
- 1x `text` entity: device label — rename the switch from HA (config category).
- 1x `number` entity: haptic feedback duration in ms, 0–65535 (config category).
- 1x `sensor` entity: WiFi RSSI in dBm (diagnostic).
- 1x `sensor` entity: firmware version string (diagnostic).
- All grouped under one HA `device`, named from the switch's label.
- Per-device availability: entities go unavailable independently if a switch loses power or WiFi, separate from bridge availability.

## Configuration options

| Option | Default | Description |
|---|---|---|
| `interactor_url` | `http://127.0.0.1:6100` | Photons Interactor HTTP endpoint |
| `poll_interval` | `5` | Seconds between relay polls |
| `backlight_poll_interval` | `30` | Seconds between backlight/label/diagnostic polls |
| `rediscovery_interval` | `300` | Seconds between re-discovery passes (picks up new switches without restart) |
| `mqtt_topic_prefix` | `lifx_switch` | MQTT topic prefix for all state and command topics |
| `log_level` | `info` | One of `debug`, `info`, `warning`, `error` |

## Caveats

- **Button-press events are not detected.** This bridge only handles relays and backlight. The LIFX LAN protocol does not push button-press notifications; events require either HomeKit Controller or Matter (both broken on switches with failed firmware updates).
- **State updates are polled, not pushed.** Default 5s polling means ~5s lag between a physical button press and HA seeing the new relay state.
- **Backlight is per-switch, not per-button.** The LIFX protocol exposes one "on" colour and one "off" colour for the whole device.

## Troubleshooting

- **No switches discovered**: check the Photons Interactor add-on log; the bridge can only find what the Interactor finds. Discovery requires HA / the Interactor to be on the same L2 as the switches.
- **MQTT auth errors**: confirm the Mosquitto broker is running and the MQTT integration is configured in HA. The supervisor injects credentials automatically; nothing to set here.
- **Backlight changes don't apply**: the Interactor must accept `pkt_type: "SetConfig"`. Test via Developer Tools -> Services -> `rest_command` with a raw call (see the project README on GitHub for examples).
- **Entities show unavailable after restart**: normal — entities start offline and come online within one `poll_interval` after the bridge connects and the first poll succeeds.

## Roadmap

- HACS-publishable custom integration replacing this MQTT bridge.
- Button-press event detection via LAN pcap analysis (if a push packet exists).
