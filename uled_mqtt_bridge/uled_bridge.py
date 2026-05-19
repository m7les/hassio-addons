#!/usr/bin/env python3
"""
ULED-AT direct MQTT bridge — bypasses the eot-controller entirely.

Each ULED-AT runs its own Mosquitto broker on port 1883 with no auth.
This service connects directly to each light, polls status, and bridges
to a local MQTT broker with Home Assistant MQTT Discovery.

Protocol recovered from ubnt/eot:1.6.1 Docker image source (obfuscated JS).
"""

import asyncio
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiomqtt
import yaml

import adoption
import discovery as disco

# ---------------------------------------------------------------------------
# Config loading — supports HA add-on (/data/options.json) and standalone
# ---------------------------------------------------------------------------

HA_OPTIONS = "/data/options.json"
STANDALONE_CONFIG = "lights.yaml"


def load_config() -> dict:
    if os.path.exists(HA_OPTIONS):
        # Running as a Home Assistant add-on.
        # Options come from /data/options.json; MQTT credentials from env.
        with open(HA_OPTIONS) as f:
            opts = json.load(f)
        return {
            "lights": opts["lights"],
            "mqtt": {
                "host": os.environ.get("MQTT_HOST", "localhost"),
                "port": int(os.environ.get("MQTT_PORT", 1883)),
                "username": os.environ.get("MQTT_USERNAME") or None,
                "password": os.environ.get("MQTT_PASSWORD") or None,
            },
            "poll_interval": int(opts.get("poll_interval", 30)),
            "discovery_prefix": opts.get("discovery_prefix", "homeassistant"),
        }

    # Standalone mode — read lights.yaml
    cfg_path = STANDALONE_CONFIG
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            f"No {HA_OPTIONS} (HA add-on) or {STANDALONE_CONFIG} (standalone) found. "
            "Copy lights.example.yaml to lights.yaml and fill in your light IPs."
        )
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    mqtt = cfg.get("mqtt", {})
    return {
        "lights": cfg["lights"],
        "mqtt": {
            "host": mqtt.get("host", "localhost"),
            "port": int(mqtt.get("port", 1883)),
            "username": mqtt.get("username") or None,
            "password": mqtt.get("password") or None,
        },
        "poll_interval": int(cfg.get("poll_interval", 30)),
        "discovery_prefix": cfg.get("discovery_prefix", "homeassistant"),
    }

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("uled")


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

def _req(target: str, value=None) -> bytes:
    payload: dict = {
        "token": uuid.uuid4().hex[:8],
        "ts": math.floor(time.time()),
        "target": target,
    }
    if value is not None:
        payload["value"] = value
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# State + model
# ---------------------------------------------------------------------------

@dataclass
class LightState:
    brightness: int = 0
    output: int = 0
    online: bool = False
    voltage: Optional[float] = None
    power: Optional[float] = None


@dataclass
class Light:
    id: str
    name: str
    ip: str
    mac: str = ""
    state: LightState = field(default_factory=LightState)


# ---------------------------------------------------------------------------
# Per-light bridge
# ---------------------------------------------------------------------------

class LightBridge:
    """
    Owns one MQTT connection to a ULED-AT device. Handles reconnection,
    status polling, and command dispatch.  Publishes state to an HA client.
    """

    def __init__(
        self,
        light: Light,
        ha: aiomqtt.Client,
        poll_interval: int,
        discovery_prefix: str,
    ):
        self.light = light
        self.ha = ha
        self.poll_interval = poll_interval
        self.discovery_prefix = discovery_prefix
        self._cmd_queue: asyncio.Queue = asyncio.Queue()
        self._logger = logging.getLogger(f"uled.{light.id}")

    async def run(self):
        """Main reconnect loop with exponential backoff."""
        delay = 10
        while True:
            try:
                await self._session()
                delay = 10
            except (aiomqtt.MqttError, OSError, ConnectionRefusedError) as exc:
                was_online = self.light.state.online
                self._logger.warning("Disconnected (%s), retry in %ds", exc, delay)
                await self._set_availability(False)
                await asyncio.sleep(delay)
                # If we were connected and then dropped, reset to fast retry.
                # If we never connected, back off to avoid log spam.
                delay = 10 if was_online else min(delay * 2, 120)

    async def send(self, topic: str, value=None):
        """Queue a command to the device (safe to call anytime)."""
        await self._cmd_queue.put((topic, value))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _session(self):
        l = self.light
        self._logger.info("Connecting to %s:1883", l.ip)

        async with aiomqtt.Client(l.ip, port=1883, keepalive=20) as dev:
            self._logger.info("Connected")
            await self._publish_discovery()
            await self._set_availability(True)

            await dev.subscribe("v2/controller/rsp/get")

            # Ask for current state right away
            await dev.publish("v2/controller/req/get/status", _req("status"))

            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._receive_loop(dev))
                tg.create_task(self._poll_loop(dev))
                tg.create_task(self._command_loop(dev))

    async def _receive_loop(self, dev: aiomqtt.Client):
        async for msg in dev.messages:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError:
                continue

            if str(msg.topic) == "v2/controller/rsp/get":
                await self._handle_status(payload)

    async def _poll_loop(self, dev: aiomqtt.Client):
        while True:
            await asyncio.sleep(self.poll_interval)
            await dev.publish("v2/controller/req/get/status", _req("status"))

    async def _command_loop(self, dev: aiomqtt.Client):
        while True:
            topic, value = await self._cmd_queue.get()
            await dev.publish(topic, _req(self._target_for(topic), value))
            self._logger.info("Sent %s value=%s", topic, value)
            # Drain any additional commands queued in the same burst before polling
            await asyncio.sleep(0.1)
            while not self._cmd_queue.empty():
                topic, value = self._cmd_queue.get_nowait()
                await dev.publish(topic, _req(self._target_for(topic), value))
                self._logger.info("Sent %s value=%s", topic, value)
            await asyncio.sleep(0.4)
            await dev.publish("v2/controller/req/get/status", _req("status"))

    async def _handle_status(self, payload: dict):
        if payload.get("target") != "status":
            return
        s = self.light.state
        changed = False
        if "led" in payload:
            b = round(int(payload["led"]) * 255 / 100)
            if s.brightness != b:
                s.brightness = b
                changed = True
        if "output" in payload:
            o = int(payload["output"])
            if s.output != o:
                s.output = o
                changed = True
        if "voltage" in payload:
            s.voltage = payload["voltage"]
        if "power" in payload:
            s.power = payload["power"]
        if changed:
            await self.publish_state()
            self._logger.info("brightness=%d output=%d", s.brightness, s.output)

    async def publish_state(self):
        s = self.light.state
        await self.ha.publish(
            f"uled/{self.light.id}/state",
            json.dumps({
                "state": "ON" if s.output else "OFF",
                "brightness": s.brightness,
            }),
            retain=True,
        )

    async def _set_availability(self, online: bool):
        self.light.state.online = online
        await self.ha.publish(
            f"uled/{self.light.id}/availability",
            "online" if online else "offline",
            retain=True,
        )

    async def _publish_discovery(self):
        l = self.light
        config = {
            "name": l.name,
            "unique_id": f"uled_{l.id}",
            "schema": "json",
            "state_topic": f"uled/{l.id}/state",
            "command_topic": f"uled/{l.id}/set",
            "brightness": True,
            "brightness_scale": 255,
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": f"uled/{l.id}/availability",
            "qos": 0,
            "device": {
                "identifiers": [l.id],
                "name": l.name,
                "manufacturer": "Ubiquiti",
                "model": "ULED-AT",
            },
        }
        await self.ha.publish(
            f"{self.discovery_prefix}/light/{l.id}/config",
            json.dumps(config),
            retain=True,
        )

    @staticmethod
    def _target_for(topic: str) -> str:
        return topic.split("/")[-1]


# ---------------------------------------------------------------------------
# HA MQTT message router (single consumer of ha.messages)
# ---------------------------------------------------------------------------

async def ha_message_router(
    ha: aiomqtt.Client,
    bridges: dict[str, "LightBridge"],
    lights: dict[str, Light],
    adopt_queue: asyncio.Queue,
    discovery_prefix: str,
):
    """
    Single consumer for all HA MQTT messages.

    Handles:
      uled/{id}/set          — light brightness/on-off commands
      uled/adopt/{mac}/set   — adoption button presses from HA UI
    """
    # Light commands for initially-configured lights
    for light_id in lights:
        await ha.subscribe(f"uled/{light_id}/set")

    # Wildcard for adopt buttons published during discovery
    await ha.subscribe("uled/adopt/+/set")

    async for msg in ha.messages:
        topic = str(msg.topic)
        parts = topic.split("/")

        # --- Adopt button: uled/adopt/<mac>/set ---
        if len(parts) == 4 and parts[0] == "uled" and parts[1] == "adopt" and parts[3] == "set":
            mac = parts[2]
            log.info("Adopt request received for MAC %s", mac)
            await adopt_queue.put(mac)
            continue

        # --- Light command: uled/<id>/set ---
        if len(parts) == 3 and parts[0] == "uled" and parts[2] == "set":
            light_id = parts[1]
            bridge = bridges.get(light_id)
            light = lights.get(light_id)
            if not bridge or not light:
                continue

            try:
                cmd = json.loads(msg.payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raw = msg.payload.decode(errors="replace").strip().upper()
                cmd = {"state": raw}

            state = cmd.get("state", "").upper()
            brightness = cmd.get("brightness")

            if state == "OFF":
                await bridge.send("v2/req/set/ledlamp/output", 0)
                light.state.output = 0
            elif state == "ON" or brightness is not None:
                if light.state.output == 0:
                    await bridge.send("v2/req/set/ledlamp/output", 1)
                    light.state.output = 1
                if brightness is not None:
                    ha_b = max(0, min(255, int(brightness)))
                    device_b = round(ha_b * 100 / 255)
                    await bridge.send("v2/req/set/ledlamp/led", device_b)
                    light.state.brightness = ha_b

            # Optimistic publish so HA stops retrying before the next poll
            await bridge.publish_state()
            log.info("Command → %s: state=%s brightness=%s", light_id, state, brightness)


# ---------------------------------------------------------------------------
# Network discovery
# ---------------------------------------------------------------------------

async def _publish_pending_device(
    ha: aiomqtt.Client,
    panel: disco.DiscoveredDevice,
    discovery_prefix: str,
):
    """
    Publish two MQTT Discovery entities for an unadopted panel:
      - A sensor showing device details (IP, MAC, firmware, etc.)
      - A button that triggers adoption when pressed from the HA UI
    Both are grouped under the same HA device card.
    """
    safe_mac = panel.mac.replace(":", "")
    device_id = f"uled_pending_{safe_mac}"
    display_name = panel.hostname or panel.mac

    ha_device = {
        "identifiers": [device_id],
        "name": display_name,
        "manufacturer": "Ubiquiti",
        "model": panel.model or panel.platform,
        "sw_version": panel.firmware,
    }

    # Sensor — shows adoption state + all attributes
    sensor_cfg = {
        "name": "Status",
        "unique_id": f"{device_id}_status",
        "state_topic": f"uled/pending/{safe_mac}/state",
        "json_attributes_topic": f"uled/pending/{safe_mac}/attrs",
        "icon": "mdi:lightbulb-question",
        "device": ha_device,
    }
    await ha.publish(
        f"{discovery_prefix}/sensor/{device_id}_status/config",
        json.dumps(sensor_cfg),
        retain=True,
    )
    await ha.publish(
        f"uled/pending/{safe_mac}/state",
        "unadopted" if not panel.adopted else "adopted_elsewhere",
        retain=True,
    )
    await ha.publish(
        f"uled/pending/{safe_mac}/attrs",
        json.dumps({
            "ip": panel.ip,
            "mac": panel.mac,
            "platform": panel.platform,
            "firmware": panel.firmware,
        }),
        retain=True,
    )

    # Button — triggers adoption
    button_cfg = {
        "name": "Adopt",
        "unique_id": f"{device_id}_adopt",
        "command_topic": f"uled/adopt/{safe_mac}/set",
        "payload_press": "adopt",
        "icon": "mdi:plus-network",
        "device": ha_device,
    }
    await ha.publish(
        f"{discovery_prefix}/button/{device_id}_adopt/config",
        json.dumps(button_cfg),
        retain=True,
    )

    log.info(
        "Surfaced unadopted panel in HA: %s (%s) at %s fw=%s",
        display_name, panel.platform, panel.ip, panel.firmware,
    )


async def _remove_pending_device(
    ha: aiomqtt.Client,
    mac: str,
    discovery_prefix: str,
):
    """Remove the pending sensor+button entities after adoption."""
    safe_mac = mac.replace(":", "")
    device_id = f"uled_pending_{safe_mac}"
    await ha.publish(f"{discovery_prefix}/sensor/{device_id}_status/config", b"", retain=True)
    await ha.publish(f"{discovery_prefix}/button/{device_id}_adopt/config", b"", retain=True)


async def discovery_loop(
    ha: aiomqtt.Client,
    lights: dict[str, Light],
    discovered: dict[str, disco.DiscoveredDevice],
    discovery_prefix: str,
    poll_interval: int,
):
    """
    Periodically scan for ULED-AT panels via Ubiquiti UDP discovery (port 10001).
    Unadopted panels that aren't already controlled are published to HA as a
    sensor + adopt button grouped under the same device card.
    """
    while True:
        log.info("Running network discovery scan...")
        try:
            panels = await disco.scan_led_panels(timeout=5.0)
            log.info("Discovery: found %d LED panel(s)", len(panels))

            # MAC → Light for adopted devices; IP set for manually configured ones
            mac_to_light = {l.mac: l for l in lights.values() if l.mac}
            manual_ips = {l.ip for l in lights.values() if not l.mac}

            for panel in panels:
                safe_mac = panel.mac.replace(":", "")

                # Adopted device — check for IP change
                managed = mac_to_light.get(safe_mac)
                if managed:
                    if managed.ip != panel.ip:
                        log.info(
                            "IP change for %s: %s → %s (updating)",
                            safe_mac, managed.ip, panel.ip,
                        )
                        managed.ip = panel.ip
                        adoption.update_adopted_ip(safe_mac, panel.ip)
                    continue

                # Manually configured light (matched by IP, no MAC stored)
                if panel.ip in manual_ips:
                    continue

                # Already surfaced as pending
                if safe_mac in discovered:
                    continue

                discovered[safe_mac] = panel
                await _publish_pending_device(ha, panel, discovery_prefix)

        except Exception as exc:
            log.warning("Discovery scan failed: %s", exc)

        await asyncio.sleep(poll_interval * 5)


# ---------------------------------------------------------------------------
# Adoption handler
# ---------------------------------------------------------------------------

async def adoption_handler(
    ha: aiomqtt.Client,
    lights: dict[str, Light],
    bridges: dict[str, LightBridge],
    discovered: dict[str, disco.DiscoveredDevice],
    adopt_queue: asyncio.Queue,
    poll_interval: int,
    discovery_prefix: str,
):
    """
    Waits for adopt requests (MAC addresses) from the message router,
    runs SSH adoption, then starts a LightBridge for the new device.
    """
    while True:
        mac = await adopt_queue.get()
        panel = discovered.get(mac)
        if not panel:
            log.warning("Adopt request for unknown MAC %s — ignoring", mac)
            continue

        log.info("Starting adoption of %s (%s) at %s", panel.hostname, panel.platform, panel.ip)

        # Update sensor state so the user sees feedback immediately
        safe_mac = mac.replace(":", "")
        await ha.publish(f"uled/pending/{safe_mac}/state", "adopting...", retain=True)

        try:
            device = await adoption.adopt(panel.ip, panel.hostname or panel.mac, mac)
        except Exception as exc:
            log.error("Adoption of %s failed: %s", panel.ip, exc)
            await ha.publish(f"uled/pending/{safe_mac}/state", "adoption_failed", retain=True)
            continue

        # Remove the pending entities from HA
        await _remove_pending_device(ha, mac, discovery_prefix)
        discovered.pop(mac, None)

        # Create a Light + LightBridge and start controlling it
        light = Light(id=device["id"], name=device["name"], ip=device["ip"], mac=device["mac"])
        lights[light.id] = light
        bridge = LightBridge(light, ha, poll_interval, discovery_prefix)
        bridges[light.id] = bridge

        # Subscribe to commands for this new light
        await ha.subscribe(f"uled/{light.id}/set")

        # Spawn the bridge task
        asyncio.create_task(bridge.run(), name=f"bridge-{light.id}")

        log.info("Adoption complete — now controlling %s as '%s'", panel.ip, light.name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    cfg = load_config()

    # Combine options-configured lights with previously adopted devices
    all_entries = list(cfg["lights"])
    persisted_macs = {e.get("mac") for e in all_entries if e.get("mac")}
    for dev in adoption.load_adopted():
        if dev.get("mac") not in persisted_macs:
            all_entries.append(dev)

    lights: dict[str, Light] = {
        e["id"]: Light(id=e["id"], name=e["name"], ip=e["ip"], mac=e.get("mac", ""))
        for e in all_entries
    }

    mqtt = cfg["mqtt"]
    ha_host = mqtt["host"]
    ha_port = mqtt["port"]
    poll_interval = cfg["poll_interval"]
    discovery_prefix = cfg["discovery_prefix"]

    log.info(
        "Connecting to HA MQTT broker at %s:%d (%d light(s) configured)",
        ha_host, ha_port, len(lights),
    )

    client_kwargs: dict = {}
    if mqtt.get("username"):
        client_kwargs["username"] = mqtt["username"]
        client_kwargs["password"] = mqtt.get("password", "")

    # Shared mutable state (adoption handler extends these live)
    bridges: dict[str, LightBridge] = {}
    discovered: dict[str, disco.DiscoveredDevice] = {}
    adopt_queue: asyncio.Queue = asyncio.Queue()

    async with aiomqtt.Client(ha_host, port=ha_port, **client_kwargs) as ha:
        # Build initial bridges
        for light in lights.values():
            bridges[light.id] = LightBridge(light, ha, poll_interval, discovery_prefix)

        # Kick off all tasks; message router owns the ha.messages loop
        async with asyncio.TaskGroup() as tg:
            for bridge in bridges.values():
                tg.create_task(bridge.run())
            tg.create_task(
                ha_message_router(ha, bridges, lights, adopt_queue, discovery_prefix)
            )
            tg.create_task(
                discovery_loop(ha, lights, discovered, discovery_prefix, poll_interval)
            )
            tg.create_task(
                adoption_handler(
                    ha, lights, bridges, discovered,
                    adopt_queue, poll_interval, discovery_prefix,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
