# ULED-AT MQTT Bridge

Controls Ubiquiti ULED-AT LED panels directly via MQTT, bypassing the `ubnt/eot`
controller entirely.

## How it works

Each ULED-AT runs its own Mosquitto broker on port 1883 with no authentication.
This add-on connects directly to each light, polls status, and publishes to
HA's MQTT broker with MQTT Discovery — lights appear as standard dimmable
entities with no extra YAML.

The protocol was recovered by reverse-engineering the obfuscated JS source
inside the `ubnt/eot:1.6.1` Docker image.

## Requirements

- [Mosquitto broker](https://github.com/home-assistant/addons/tree/master/mosquitto) add-on

## Configuration

```yaml
lights:
  - id: uled_1
    name: "Office Light 1"
    ip: "192.168.1.50"
  - id: uled_2
    name: "Office Light 2"
    ip: "192.168.1.51"
poll_interval: 30
discovery_prefix: homeassistant
```

| Option             | Default           | Description                              |
|--------------------|-------------------|------------------------------------------|
| `lights`           | —                 | List of lights with `id`, `name`, `ip`   |
| `poll_interval`    | `30`              | Seconds between status polls             |
| `discovery_prefix` | `homeassistant`   | HA MQTT Discovery prefix                 |

## Troubleshooting

**Light shows unavailable** — confirm port 1883 is reachable on the light's IP.

**Can run alongside eot-controller** — both connect as MQTT clients to the
light's broker and coexist fine.
