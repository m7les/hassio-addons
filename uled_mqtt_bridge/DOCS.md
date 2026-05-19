# ULED-AT MQTT Bridge

Controls Ubiquiti ULED-AT LED panels directly via MQTT, bypassing the `ubnt/eot`
controller entirely. Panels appear in Home Assistant as dimmable light entities.

## First-time setup

1. Install the add-on and start it — no configuration needed.
2. Go to **Settings → Devices & Services → MQTT** and look for new device cards.
3. Each undiscovered panel appears with a **Status** sensor and an **Adopt** button.
4. Press **Adopt**. The add-on SSHes into the panel with factory credentials,
   marks it as managed, and immediately starts controlling it. The light entity
   appears without a restart.

Adopted devices are saved to `/data/adopted.json` and restored on every startup.

## Configuration options

| Option             | Default         | Description                                                  |
|--------------------|-----------------|--------------------------------------------------------------|
| `lights`           | `[]`            | Optional pre-configured lights (`id`, `name`, `ip`). Leave  |
|                    |                 | empty to rely entirely on discovery and adoption.            |
| `poll_interval`    | `30`            | Seconds between status polls from each device (5–300).       |
| `discovery_prefix` | `homeassistant` | MQTT Discovery prefix — change only if you customised HA's.  |

## Pre-configuring lights

If you know your lights' IPs and prefer not to use the adoption flow, you can
add them manually:

```yaml
lights:
  - id: uled_office
    name: "Office"
    ip: "10.0.0.215"
  - id: uled_meeting
    name: "Meeting Room"
    ip: "10.0.0.117"
```

## Troubleshooting

**Light shows unavailable** — check that TCP port 1883 is reachable on the
device's IP. The add-on needs `host_network: true` (already set) to reach
devices on other VLANs.

**Adopt fails** — the panel must still have factory SSH credentials (`ubnt`/`ubnt`).
Panels that were previously managed by a different controller may not accept adoption.

**IP changed after DHCP renewal** — the add-on detects IP changes via the
periodic discovery scan and updates automatically. No manual action needed.
