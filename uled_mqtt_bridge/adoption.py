"""
SSH-based adoption of unadopted ULED-AT devices.

Adoption marks the device as managed by this controller and persists
it to /data/adopted.json so it survives add-on restarts.

SSH credentials on factory/unadopted devices are always ubnt/ubnt
(recovered from ubnt/eot:1.6.1 config source).

The adoption sequence mirrors what the eot-controller does over SSH:
  syswrapper.sh set-eot-adopt-state 1
  syswrapper.sh set-led-ctrler-uid <uuid>
  syswrapper.sh save-eot-config
"""

import json
import logging
import os
import uuid
from dataclasses import asdict

import asyncssh

log = logging.getLogger("uled.adoption")

# Persistent storage (HA add-on /data dir; falls back to ./data/ standalone)
_DATA_DIR = "/data" if os.path.isdir("/data") else "./data"
os.makedirs(_DATA_DIR, exist_ok=True)

_CONTROLLER_ID_FILE = os.path.join(_DATA_DIR, "controller_id.json")
_ADOPTED_FILE = os.path.join(_DATA_DIR, "adopted.json")

DEFAULT_SSH_USER = "ubnt"
DEFAULT_SSH_PASS = "ubnt"


# ---------------------------------------------------------------------------
# Controller identity (stable UUID that represents "us" to devices)
# ---------------------------------------------------------------------------

def get_controller_id() -> str:
    try:
        with open(_CONTROLLER_ID_FILE) as f:
            return json.load(f)["id"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        cid = str(uuid.uuid4())
        with open(_CONTROLLER_ID_FILE, "w") as f:
            json.dump({"id": cid}, f)
        log.info("Generated controller ID: %s", cid)
        return cid


# ---------------------------------------------------------------------------
# Adopted device store
# ---------------------------------------------------------------------------

def load_adopted() -> list[dict]:
    try:
        with open(_ADOPTED_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_adopted(devices: list[dict]):
    with open(_ADOPTED_FILE, "w") as f:
        json.dump(devices, f, indent=2)


def add_adopted_device(device: dict):
    """Upsert a device into the adopted store (keyed by mac)."""
    devices = load_adopted()
    devices = [d for d in devices if d.get("mac") != device["mac"]]
    devices.append(device)
    save_adopted(devices)
    log.info("Persisted adopted device %s (%s)", device.get("hostname"), device.get("ip"))


def remove_adopted_device(mac: str):
    devices = [d for d in load_adopted() if d.get("mac") != mac]
    save_adopted(devices)


def update_adopted_ip(mac: str, new_ip: str):
    devices = load_adopted()
    for d in devices:
        if d.get("mac") == mac:
            d["ip"] = new_ip
            break
    save_adopted(devices)


# ---------------------------------------------------------------------------
# SSH adoption
# ---------------------------------------------------------------------------

async def adopt(ip: str, hostname: str, mac: str) -> dict:
    """
    SSH into the device and run the adoption commands.
    Returns the adopted device dict on success, raises on failure.

    The device must be unadopted (ssh creds ubnt/ubnt).
    We intentionally do NOT rewrite mosquitto.conf — our bridge connects
    directly to the device's MQTT broker, so the outbound bridge target
    doesn't matter for us.
    """
    controller_id = get_controller_id()
    log.info("Adopting %s (%s) with controller ID %s", hostname, ip, controller_id)

    connect_kwargs = dict(
        host=ip,
        username=DEFAULT_SSH_USER,
        password=DEFAULT_SSH_PASS,
        known_hosts=None,       # Trust any host key (local LAN devices)
        connect_timeout=15,
    )

    async with asyncssh.connect(**connect_kwargs) as conn:
        # Mark device as adopted and associate it with our controller UUID
        for cmd in [
            "syswrapper.sh set-eot-adopt-state 1",
            f"syswrapper.sh set-led-ctrler-uid {controller_id}",
            "syswrapper.sh save-eot-config",
        ]:
            result = await conn.run(cmd, check=False)
            if result.exit_status != 0:
                log.warning("Command '%s' exited %d: %s", cmd, result.exit_status, result.stderr)
            else:
                log.debug("OK: %s", cmd)

    device = {
        "id": f"uled_{mac.replace(':', '')}",
        "name": hostname or f"ULED {mac[-5:].replace(':', '')}",
        "ip": ip,
        "mac": mac,
    }
    add_adopted_device(device)
    log.info("Adoption complete: %s", device)
    return device


async def unadopt(ip: str, mac: str):
    """
    Reset adoption state on device and remove from local store.
    Uses ubnt/ubnt if still reachable (best-effort — removes from store regardless).
    """
    try:
        async with asyncssh.connect(
            host=ip,
            username=DEFAULT_SSH_USER,
            password=DEFAULT_SSH_PASS,
            known_hosts=None,
            connect_timeout=10,
        ) as conn:
            await conn.run("syswrapper.sh set-eot-adopt-state 0", check=False)
            await conn.run("syswrapper.sh set-led-ctrler-uid \"\"", check=False)
            await conn.run("syswrapper.sh save-eot-config", check=False)
    except Exception as exc:
        log.warning("Could not SSH to %s for unadoption (removing locally anyway): %s", ip, exc)
    remove_adopted_device(mac)
    log.info("Unadopted %s", mac)
