#!/usr/bin/env python3
"""LIFX Switch -> MQTT bridge via Photons Interactor.

Publishes MQTT-discovery configs for each LIFX switch's relays,
backlights, label, haptic duration, and diagnostics. Translates HA
commands to Photons HTTP calls and polls state on an interval.
"""
import asyncio
import json
import logging
import math
import os
import signal
from contextlib import suppress
from dataclasses import dataclass

import httpx
from aiomqtt import Client as MQTTClient, MqttError, Will

INTERACTOR = os.environ["INTERACTOR_URL"]
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))
BACKLIGHT_POLL_INTERVAL = float(os.environ.get("BACKLIGHT_POLL_INTERVAL", "30"))
REDISCOVERY_INTERVAL = float(os.environ.get("REDISCOVERY_INTERVAL", "300"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "lifx_switch")

DISC_PREFIX = "homeassistant"
AVAIL_TOPIC = f"{TOPIC_PREFIX}/bridge/availability"

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lifx-bridge")

_CACHE_DEFAULTS = {
    "on_hue": 0, "on_sat": 0, "on_bri": 32768, "on_kelvin": 3500,
    "off_hue": 0, "off_sat": 0, "off_bri": 4096, "off_kelvin": 3500,
    "haptic": 50,
}


@dataclass
class Switch:
    serial: str
    label: str
    relay_count: int = 4


async def interactor_call(client: httpx.AsyncClient, body: dict) -> dict:
    r = await client.put(f"{INTERACTOR}/v1/lifx/command", json=body, timeout=10)
    r.raise_for_status()
    return r.json()


async def discover_switches(client: httpx.AsyncClient) -> list[Switch]:
    resp = await interactor_call(client, {"command": "discover"})
    switches = []
    for serial, info in resp.items():
        if not isinstance(info, dict) or not serial.startswith("d073d5"):
            continue
        if "relays" in info.get("cap", []):
            label = info.get("label") or info.get("product_name") or serial
            switches.append(Switch(serial=serial, label=label))
            log.info("Discovered switch %s: %s", serial, label)
    return switches


def _serial_to_mac(serial: str) -> str:
    return ":".join(serial[i:i + 2] for i in range(0, 12, 2))


def _device_block(sw: Switch) -> dict:
    return {
        "identifiers": [sw.serial],
        "name": f"LIFX Switch — {sw.label}",
        "manufacturer": "LIFX",
        "model": "Switch",
        "connections": [["mac", _serial_to_mac(sw.serial)]],
    }


def _availability(sw: Switch) -> list[dict]:
    return [
        {"topic": AVAIL_TOPIC},
        {"topic": f"{TOPIC_PREFIX}/{sw.serial}/availability"},
    ]


def discovery_payload_relay(sw: Switch, idx: int) -> tuple[str, dict]:
    topic = f"{DISC_PREFIX}/switch/{sw.serial}_relay_{idx}/config"
    payload = {
        "name": f"Relay {idx + 1}",
        "unique_id": f"{sw.serial}_relay_{idx}",
        "state_topic": f"{TOPIC_PREFIX}/{sw.serial}/relay/{idx}/state",
        "command_topic": f"{TOPIC_PREFIX}/{sw.serial}/relay/{idx}/set",
        "payload_on": "ON",
        "payload_off": "OFF",
        "availability": _availability(sw),
        "availability_mode": "all",
        "device": _device_block(sw),
    }
    return topic, payload


def discovery_payload_backlight(sw: Switch, which: str) -> tuple[str, dict]:
    topic = f"{DISC_PREFIX}/light/{sw.serial}_backlight_{which}/config"
    payload = {
        "name": f"Backlight ({which}-state)",
        "unique_id": f"{sw.serial}_backlight_{which}",
        "schema": "json",
        "state_topic": f"{TOPIC_PREFIX}/{sw.serial}/backlight/{which}/state",
        "command_topic": f"{TOPIC_PREFIX}/{sw.serial}/backlight/{which}/set",
        "brightness": True,
        "supported_color_modes": ["hs", "color_temp"],
        "min_mireds": 111,
        "max_mireds": 667,
        "availability": _availability(sw),
        "availability_mode": "all",
        "device": _device_block(sw),
    }
    return topic, payload


def discovery_payload_label(sw: Switch) -> tuple[str, dict]:
    topic = f"{DISC_PREFIX}/text/{sw.serial}_label/config"
    payload = {
        "name": "Label",
        "unique_id": f"{sw.serial}_label",
        "state_topic": f"{TOPIC_PREFIX}/{sw.serial}/label/state",
        "command_topic": f"{TOPIC_PREFIX}/{sw.serial}/label/set",
        "max": 32,
        "availability": _availability(sw),
        "availability_mode": "all",
        "device": _device_block(sw),
        "entity_category": "config",
    }
    return topic, payload


def discovery_payload_haptic(sw: Switch) -> tuple[str, dict]:
    topic = f"{DISC_PREFIX}/number/{sw.serial}_haptic/config"
    payload = {
        "name": "Haptic Duration",
        "unique_id": f"{sw.serial}_haptic",
        "state_topic": f"{TOPIC_PREFIX}/{sw.serial}/haptic/state",
        "command_topic": f"{TOPIC_PREFIX}/{sw.serial}/haptic/set",
        "min": 0,
        "max": 65535,
        "step": 1,
        "unit_of_measurement": "ms",
        "availability": _availability(sw),
        "availability_mode": "all",
        "device": _device_block(sw),
        "entity_category": "config",
    }
    return topic, payload


def discovery_payload_rssi(sw: Switch) -> tuple[str, dict]:
    topic = f"{DISC_PREFIX}/sensor/{sw.serial}_rssi/config"
    payload = {
        "name": "WiFi RSSI",
        "unique_id": f"{sw.serial}_rssi",
        "state_topic": f"{TOPIC_PREFIX}/{sw.serial}/rssi/state",
        "device_class": "signal_strength",
        "unit_of_measurement": "dBm",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "availability": _availability(sw),
        "availability_mode": "all",
        "device": _device_block(sw),
    }
    return topic, payload


def discovery_payload_firmware(sw: Switch) -> tuple[str, dict]:
    topic = f"{DISC_PREFIX}/sensor/{sw.serial}_firmware/config"
    payload = {
        "name": "Firmware",
        "unique_id": f"{sw.serial}_firmware",
        "state_topic": f"{TOPIC_PREFIX}/{sw.serial}/firmware/state",
        "entity_category": "diagnostic",
        "availability": _availability(sw),
        "availability_mode": "all",
        "device": _device_block(sw),
    }
    return topic, payload


async def publish_discovery(mqtt: MQTTClient, switches: list[Switch]) -> None:
    for sw in switches:
        for idx in range(sw.relay_count):
            topic, payload = discovery_payload_relay(sw, idx)
            await mqtt.publish(topic, json.dumps(payload), retain=True)
        for which in ("on", "off"):
            topic, payload = discovery_payload_backlight(sw, which)
            await mqtt.publish(topic, json.dumps(payload), retain=True)
        topic, payload = discovery_payload_label(sw)
        await mqtt.publish(topic, json.dumps(payload), retain=True)
        topic, payload = discovery_payload_haptic(sw)
        await mqtt.publish(topic, json.dumps(payload), retain=True)
        topic, payload = discovery_payload_rssi(sw)
        await mqtt.publish(topic, json.dumps(payload), retain=True)
        topic, payload = discovery_payload_firmware(sw)
        await mqtt.publish(topic, json.dumps(payload), retain=True)
        # Mark offline until the first successful poll confirms reachability.
        await mqtt.publish(
            f"{TOPIC_PREFIX}/{sw.serial}/availability", "offline", retain=True
        )
        log.info("Published discovery for %s", sw.serial)


async def poll_relay(mqtt, http, sw, idx) -> bool:
    resp = await interactor_call(http, {
        "command": "query",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "GetRPower",
            "pkt_args": {"relay_index": idx},
        },
    })
    results = resp.get("results", {})
    if not results:
        return False
    payload = next(iter(results.values())).get("payload", {})
    level = payload.get("level", 0)
    state = "ON" if level > 0 else "OFF"
    await mqtt.publish(
        f"{TOPIC_PREFIX}/{sw.serial}/relay/{idx}/state", state, retain=True
    )
    return True


async def poll_backlight(mqtt, http, sw, cache):
    resp = await interactor_call(http, {
        "command": "query",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "GetConfig",
        },
    })
    results = resp.get("results", {})
    if not results:
        return
    payload = next(iter(results.values())).get("payload", {})

    current = cache.setdefault(sw.serial, dict(_CACHE_DEFAULTS))
    haptic = payload.get("haptic_duration_ms", 50)
    current["haptic"] = haptic
    await mqtt.publish(
        f"{TOPIC_PREFIX}/{sw.serial}/haptic/state", str(haptic), retain=True
    )

    for which in ("on", "off"):
        hue = payload.get(f"backlight_{which}_color_hue", 0)
        sat = payload.get(f"backlight_{which}_color_saturation", 0)
        bri = payload.get(f"backlight_{which}_color_brightness", 0)
        kelvin = payload.get(f"backlight_{which}_color_kelvin", 3500)
        current[f"{which}_hue"] = hue
        current[f"{which}_sat"] = sat
        current[f"{which}_bri"] = bri
        current[f"{which}_kelvin"] = kelvin
        state_msg = {
            "state": "ON" if bri > 0 else "OFF",
            "brightness": round(bri * 255 / 65535),
            "color_mode": "color_temp" if sat == 0 else "hs",
            "color": {
                "h": round(hue * 360 / 65535, 1),
                "s": round(sat * 100 / 65535, 1),
            },
            "color_temp": round(1_000_000 / kelvin) if kelvin else 333,
        }
        await mqtt.publish(
            f"{TOPIC_PREFIX}/{sw.serial}/backlight/{which}/state",
            json.dumps(state_msg),
            retain=True,
        )


async def poll_label(mqtt, http, sw):
    resp = await interactor_call(http, {
        "command": "query",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "GetLabel",
        },
    })
    results = resp.get("results", {})
    if not results:
        return
    label = next(iter(results.values())).get("payload", {}).get("label", "")
    await mqtt.publish(
        f"{TOPIC_PREFIX}/{sw.serial}/label/state", label, retain=True
    )


async def poll_diagnostics(mqtt, http, sw):
    try:
        resp = await interactor_call(http, {
            "command": "query",
            "args": {"matcher": {"serial": sw.serial}, "pkt_type": "GetWifiInfo"},
        })
        results = resp.get("results", {})
        if results:
            signal = next(iter(results.values())).get("payload", {}).get("signal", 0)
            rssi = round(10 * math.log10(signal), 1) if signal > 0 else -100
            await mqtt.publish(
                f"{TOPIC_PREFIX}/{sw.serial}/rssi/state", str(rssi), retain=True
            )
    except Exception:
        log.debug("GetWifiInfo failed for %s", sw.serial)

    try:
        resp = await interactor_call(http, {
            "command": "query",
            "args": {"matcher": {"serial": sw.serial}, "pkt_type": "GetHostFirmware"},
        })
        results = resp.get("results", {})
        if results:
            p = next(iter(results.values())).get("payload", {})
            await mqtt.publish(
                f"{TOPIC_PREFIX}/{sw.serial}/firmware/state",
                f"{p.get('version_major', 0)}.{p.get('version_minor', 0)}",
                retain=True,
            )
    except Exception:
        log.debug("GetHostFirmware failed for %s", sw.serial)


async def poll_loop(mqtt, http, switches, cache):
    counter = 0
    backlight_every = max(1, int(BACKLIGHT_POLL_INTERVAL / POLL_INTERVAL))
    device_online: dict[str, bool] = {}

    while True:
        for sw in list(switches):
            try:
                relay_ok = False
                for idx in range(sw.relay_count):
                    if await poll_relay(mqtt, http, sw, idx):
                        relay_ok = True
                if counter % backlight_every == 0:
                    await poll_backlight(mqtt, http, sw, cache)
                    await poll_label(mqtt, http, sw)
                    await poll_diagnostics(mqtt, http, sw)

                was = device_online.get(sw.serial)
                if relay_ok != was:
                    status = "online" if relay_ok else "offline"
                    await mqtt.publish(
                        f"{TOPIC_PREFIX}/{sw.serial}/availability", status, retain=True
                    )
                    device_online[sw.serial] = relay_ok
                    log.info("Switch %s is %s", sw.serial, status)
            except Exception:
                log.exception("poll error for %s", sw.serial)
                if device_online.get(sw.serial) is not False:
                    await mqtt.publish(
                        f"{TOPIC_PREFIX}/{sw.serial}/availability", "offline", retain=True
                    )
                    device_online[sw.serial] = False

        counter += 1
        await asyncio.sleep(POLL_INTERVAL)


async def rediscovery_loop(mqtt, http, switches, by_serial):
    while True:
        await asyncio.sleep(REDISCOVERY_INTERVAL)
        try:
            discovered = await discover_switches(http)
            for sw in discovered:
                if sw.serial not in by_serial:
                    log.info("New switch found: %s (%s)", sw.serial, sw.label)
                    switches.append(sw)
                    by_serial[sw.serial] = sw
                    await publish_discovery(mqtt, [sw])
        except Exception:
            log.exception("rediscovery error")


async def handle_relay_cmd(mqtt, http, sw, idx, payload):
    level = 65535 if payload.upper() == "ON" else 0
    await interactor_call(http, {
        "command": "set",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "SetRPower",
            "pkt_args": {"relay_index": idx, "level": level},
        },
    })
    await poll_relay(mqtt, http, sw, idx)


def _setconfig_args(current: dict) -> dict:
    return {
        "haptic_duration_ms": current["haptic"],
        "backlight_on_color_hue": current["on_hue"],
        "backlight_on_color_saturation": current["on_sat"],
        "backlight_on_color_brightness": current["on_bri"],
        "backlight_on_color_kelvin": current["on_kelvin"],
        "backlight_off_color_hue": current["off_hue"],
        "backlight_off_color_saturation": current["off_sat"],
        "backlight_off_color_brightness": current["off_bri"],
        "backlight_off_color_kelvin": current["off_kelvin"],
    }


async def handle_backlight_cmd(mqtt, http, sw, which, payload, cache):
    try:
        cmd = json.loads(payload)
    except json.JSONDecodeError:
        cmd = {"state": payload}

    current = cache.setdefault(sw.serial, dict(_CACHE_DEFAULTS))
    prefix = f"{which}_"

    if "brightness" in cmd:
        current[prefix + "bri"] = int(cmd["brightness"] * 65535 / 255)
    elif cmd.get("state") == "OFF":
        current[prefix + "bri"] = 0
    elif cmd.get("state") == "ON" and current[prefix + "bri"] == 0:
        current[prefix + "bri"] = 32768

    if "color" in cmd and isinstance(cmd["color"], dict):
        if "h" in cmd["color"]:
            current[prefix + "hue"] = int(cmd["color"]["h"] * 65535 / 360)
        if "s" in cmd["color"]:
            current[prefix + "sat"] = int(cmd["color"]["s"] * 65535 / 100)

    if "color_temp" in cmd:
        current[prefix + "kelvin"] = int(1_000_000 / cmd["color_temp"])
        current[prefix + "sat"] = 0

    await interactor_call(http, {
        "command": "set",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "SetConfig",
            "pkt_args": _setconfig_args(current),
        },
    })
    await poll_backlight(mqtt, http, sw, cache)


async def handle_label_cmd(mqtt, http, sw, payload):
    label = payload.strip()[:32]
    await interactor_call(http, {
        "command": "set",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "SetLabel",
            "pkt_args": {"label": label},
        },
    })
    sw.label = label
    await mqtt.publish(
        f"{TOPIC_PREFIX}/{sw.serial}/label/state", label, retain=True
    )
    await publish_discovery(mqtt, [sw])


async def handle_haptic_cmd(mqtt, http, sw, payload, cache):
    try:
        ms = max(0, min(65535, int(float(payload))))
    except (ValueError, TypeError):
        return
    current = cache.setdefault(sw.serial, dict(_CACHE_DEFAULTS))
    current["haptic"] = ms
    await interactor_call(http, {
        "command": "set",
        "args": {
            "matcher": {"serial": sw.serial},
            "pkt_type": "SetConfig",
            "pkt_args": _setconfig_args(current),
        },
    })
    await mqtt.publish(
        f"{TOPIC_PREFIX}/{sw.serial}/haptic/state", str(ms), retain=True
    )


async def command_loop(mqtt, http, by_serial, cache):
    await mqtt.subscribe(f"{TOPIC_PREFIX}/+/relay/+/set")
    await mqtt.subscribe(f"{TOPIC_PREFIX}/+/backlight/+/set")
    await mqtt.subscribe(f"{TOPIC_PREFIX}/+/label/set")
    await mqtt.subscribe(f"{TOPIC_PREFIX}/+/haptic/set")

    async for message in mqtt.messages:
        topic = message.topic.value
        payload = message.payload.decode()
        parts = topic.split("/")
        try:
            _, serial, kind, *rest = parts
            sw = by_serial.get(serial)
            if not sw:
                continue
            if kind == "relay":
                await handle_relay_cmd(mqtt, http, sw, int(rest[0]), payload)
            elif kind == "backlight":
                await handle_backlight_cmd(mqtt, http, sw, rest[0], payload, cache)
            elif kind == "label":
                await handle_label_cmd(mqtt, http, sw, payload)
            elif kind == "haptic":
                await handle_haptic_cmd(mqtt, http, sw, payload, cache)
        except Exception:
            log.exception("command error on %s: %r", topic, payload)


async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    async with httpx.AsyncClient() as http:
        switches = await discover_switches(http)
        if not switches:
            log.error("No switches found via Interactor at %s", INTERACTOR)
            return

        by_serial: dict[str, Switch] = {sw.serial: sw for sw in switches}
        cache: dict = {}

        while not stop_event.is_set():
            try:
                will = Will(topic=AVAIL_TOPIC, payload="offline", retain=True)
                async with MQTTClient(
                    hostname=MQTT_HOST,
                    port=MQTT_PORT,
                    username=MQTT_USER or None,
                    password=MQTT_PASS or None,
                    will=will,
                ) as mqtt:
                    log.info("Connected to MQTT %s:%d", MQTT_HOST, MQTT_PORT)
                    await publish_discovery(mqtt, switches)
                    await mqtt.publish(AVAIL_TOPIC, "online", retain=True)
                    tasks = [
                        asyncio.create_task(poll_loop(mqtt, http, switches, cache)),
                        asyncio.create_task(command_loop(mqtt, http, by_serial, cache)),
                        asyncio.create_task(rediscovery_loop(mqtt, http, switches, by_serial)),
                        asyncio.create_task(stop_event.wait()),
                    ]
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
                        with suppress(asyncio.CancelledError):
                            await t
                    await mqtt.publish(AVAIL_TOPIC, "offline", retain=True)
            except MqttError:
                log.exception("MQTT lost; reconnecting in 5s")
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
