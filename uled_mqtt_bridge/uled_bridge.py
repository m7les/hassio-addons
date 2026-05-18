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
        """Main reconnect loop."""
        while True:
            try:
                await self._session()
            except (aiomqtt.MqttError, OSError, ConnectionRefusedError) as exc:
                self._logger.warning("Disconnected (%s), retry in 10s", exc)
                await self._set_availability(False)
                await asyncio.sleep(10)

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
            await dev.subscribe("v2/rsp/#")

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

    async def _handle_status(self, payload: dict):
        if payload.get("target") != "status":
            return
        s = self.light.state
        changed = False
        if "led" in payload:
            b = int(payload["led"])
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
            await self._publish_ha_state()
            self._logger.info("brightness=%d output=%d", s.brightness, s.output)

    async def _publish_ha_state(self):
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
# HA command router
# ---------------------------------------------------------------------------

async def ha_command_router(
    ha: aiomqtt.Client,
    bridges: dict[str, LightBridge],
    lights: dict[str, Light],
):
    """Listen on uled/{id}/set and route commands to the right LightBridge."""
    for light_id in lights:
        await ha.subscribe(f"uled/{light_id}/set")

    async for msg in ha.messages:
        parts = str(msg.topic).split("/")
        if len(parts) != 3 or parts[2] != "set":
            continue

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
                b = max(0, min(255, int(brightness)))
                await bridge.send("v2/req/set/ledlamp/led", b)
                light.state.brightness = b

        log.info("Command → %s: state=%s brightness=%s", light_id, state, brightness)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    cfg = load_config()

    lights = {
        entry["id"]: Light(id=entry["id"], name=entry["name"], ip=entry["ip"])
        for entry in cfg["lights"]
    }

    mqtt = cfg["mqtt"]
    ha_host = mqtt["host"]
    ha_port = mqtt["port"]
    poll_interval = cfg["poll_interval"]
    discovery_prefix = cfg["discovery_prefix"]

    log.info("Connecting to HA MQTT broker at %s:%d", ha_host, ha_port)

    client_kwargs = {}
    if mqtt.get("username"):
        client_kwargs["username"] = mqtt["username"]
        client_kwargs["password"] = mqtt.get("password", "")

    async with aiomqtt.Client(ha_host, port=ha_port, **client_kwargs) as ha:
        bridges = {
            lid: LightBridge(light, ha, poll_interval, discovery_prefix)
            for lid, light in lights.items()
        }

        async with asyncio.TaskGroup() as tg:
            for bridge in bridges.values():
                tg.create_task(bridge.run())
            tg.create_task(ha_command_router(ha, bridges, lights))


if __name__ == "__main__":
    asyncio.run(main())
