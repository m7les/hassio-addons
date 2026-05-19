# ULED-AT MQTT Bridge

Controls Ubiquiti ULED-AT LED panels directly via MQTT, bypassing the `ubnt/eot`
controller entirely.

## How it works

Each ULED-AT runs its own Mosquitto broker on port 1883 with no authentication.
This add-on connects directly to each light, polls status, and publishes to
HA's MQTT broker with MQTT Discovery — lights appear as standard dimmable
entities with no extra YAML.

Unadopted panels are discovered via the Ubiquiti UDP protocol (port 10001) and
surfaced in HA as device cards with an **Adopt** button. Pressing Adopt SSHes
into the device with the factory credentials (`ubnt`/`ubnt`), marks it as
managed, and immediately starts controlling it — no restart required.

The protocol was recovered by reverse-engineering the obfuscated JS source
inside the `ubnt/eot:1.6.1` Docker image.

## Requirements

- [Mosquitto broker](https://github.com/home-assistant/addons/tree/master/mosquitto) add-on

## Configuration

```yaml
lights: []          # leave empty to rely on discovery/adoption
poll_interval: 30
discovery_prefix: homeassistant
```

| Option             | Default         | Description                                                   |
|--------------------|-----------------|---------------------------------------------------------------|
| `lights`           | `[]`            | Optional list of pre-configured lights (`id`, `name`, `ip`)  |
| `poll_interval`    | `30`            | Seconds between status polls (5–300)                         |
| `discovery_prefix` | `homeassistant` | HA MQTT Discovery prefix                                      |

Adopted devices are persisted to `/data/adopted.json` and restored on startup.
If a device's IP changes (DHCP), the add-on detects the new IP via the next
discovery scan and heals the connection automatically.

## Troubleshooting

**Light shows unavailable** — confirm port 1883 is reachable on the light's IP.  
**Adopt button does nothing** — confirm the device is on factory firmware with
default SSH credentials (`ubnt`/`ubnt`).  
**Can run alongside eot-controller** — both connect as MQTT clients to the
light's broker and coexist fine.
