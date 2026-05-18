"""
Ubiquiti device discovery — UDP port 10001 (standard ubnt discovery protocol).

Sends a 4-byte probe to broadcast (255.255.255.255) and multicast
(233.89.188.1) then parses TLV-encoded responses to find ULED-AT panels.

Protocol recovered from ubnt/eot:1.6.1 Docker image (discovery/src/versions/v1.js).
"""

import asyncio
import ipaddress
import logging
import socket
import struct
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("uled.discovery")

DISCOVERY_PORT = 10001
BCAST_ADDR = "255.255.255.255"
MCAST_ADDR = "233.89.188.1"
SCAN_TIMEOUT = 5.0   # seconds per pass; up to 3 passes

# TLV type codes (from v1.js)
TLV_HWADDR   = 0x01  # MAC (6 bytes) + IP (4 bytes) = 10 bytes
TLV_IPINFO   = 0x02
TLV_FWVER    = 0x03
TLV_HOSTNAME = 0x0b
TLV_PLATFORM = 0x0c
TLV_WMODE    = 0x0e
TLV_MODEL    = 0x14
TLV_IS_DEFAULT = 0x17   # MGMT_IS_DEFAULT — 1 = unadopted
TLV_ETH_MAC  = 0x01
TLV_ADOPTED_BY = 0x2e   # 16-byte UUID of adopting controller

# Platform strings that identify ULED-AT LED panels
LED_PLATFORMS = {"ULP2PE", "ULP2AC", "ULP2AC2", "ULP2AC3", "ULP2PE2"}
DIMMER_PLATFORMS = {"ULDPE", "ULDPE2", "ULDAC", "ULDAC2"}

# 4-byte discovery probe: version=1, cmd=0 (CMD_INFO), length=0
PROBE = struct.pack(">BBBB", 0x01, 0x00, 0x00, 0x00)


@dataclass
class DiscoveredDevice:
    ip: str
    mac: str           # wifi/primary MAC
    eth_mac: str       # ethernet MAC
    hostname: str
    platform: str      # e.g. "ULP2PE"
    model: str
    firmware: str
    adopted: bool      # True if managed by a controller
    adopted_by: Optional[str] = None   # controller UUID if known


def _parse_mac(data: bytes) -> str:
    return ":".join(f"{b:02x}" for b in data)


def _parse_ip(data: bytes) -> str:
    return socket.inet_ntoa(data)


def _parse_tlv(payload: bytes) -> dict:
    """Parse a TLV-encoded discovery response into a dict of fields."""
    fields: dict[int, list[bytes]] = {}
    i = 0
    while i < len(payload):
        if i + 3 > len(payload):
            break
        tlv_type = payload[i]
        tlv_len = struct.unpack_from(">H", payload, i + 1)[0]
        i += 3
        if i + tlv_len > len(payload):
            break
        value = payload[i: i + tlv_len]
        fields.setdefault(tlv_type, []).append(value)
        i += tlv_len
    return fields


def _decode_response(data: bytes, src_ip: str) -> Optional[DiscoveredDevice]:
    """Turn a raw UDP response into a DiscoveredDevice, or None if invalid."""
    if len(data) < 4:
        return None

    version = data[0]
    cmd = data[1]
    payload_len = struct.unpack_from(">H", data, 2)[0]

    if version != 1 or cmd != 0:
        return None

    payload = data[4: 4 + payload_len]
    tlv = _parse_tlv(payload)

    mac = ""
    eth_mac = ""
    ip = src_ip

    # TLV_HWADDR (0x01): 6-byte MAC + 4-byte IP
    for val in tlv.get(TLV_HWADDR, []):
        if len(val) == 10:
            if not mac:
                mac = _parse_mac(val[:6])
            if not ip or ip == src_ip:
                ip = _parse_ip(val[6:10])
        elif len(val) == 6 and not eth_mac:
            eth_mac = _parse_mac(val)

    hostname = ""
    for val in tlv.get(TLV_HOSTNAME, []):
        hostname = val.decode(errors="replace").strip("\x00")

    platform = ""
    for val in tlv.get(TLV_PLATFORM, []):
        platform = val.decode(errors="replace").strip("\x00")

    model = ""
    for val in tlv.get(TLV_MODEL, []):
        model = val.decode(errors="replace").strip("\x00")

    firmware = ""
    for val in tlv.get(TLV_FWVER, []):
        firmware = val.decode(errors="replace").strip("\x00")

    # MGMT_IS_DEFAULT: 1 means factory/unadopted
    adopted = True
    for val in tlv.get(TLV_IS_DEFAULT, []):
        if len(val) >= 1:
            adopted = val[0] == 0   # 0 = has been adopted/managed

    adopted_by = None
    for val in tlv.get(TLV_ADOPTED_BY, []):
        if len(val) == 16 and any(b != 0 for b in val):
            adopted_by = val.hex()

    if not platform:
        return None

    return DiscoveredDevice(
        ip=ip,
        mac=mac or eth_mac,
        eth_mac=eth_mac or mac,
        hostname=hostname,
        platform=platform,
        model=model,
        firmware=firmware,
        adopted=adopted,
        adopted_by=adopted_by,
    )


async def scan(timeout: float = SCAN_TIMEOUT) -> list[DiscoveredDevice]:
    """
    Broadcast a discovery probe and collect responses for `timeout` seconds.
    Returns all responding Ubiquiti devices (not filtered by platform).
    """
    loop = asyncio.get_running_loop()
    found: dict[str, DiscoveredDevice] = {}  # keyed by MAC

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setblocking(False)
    sock.bind(("", 0))

    transport, protocol = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        sock=sock,
    )

    class _Protocol(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            src_ip = addr[0]
            device = _decode_response(data, src_ip)
            if device and device.mac and device.mac not in found:
                found[device.mac] = device
                log.debug("Discovered %s %s at %s (adopted=%s)",
                          device.platform, device.hostname, device.ip, device.adopted)

        def error_received(self, exc):
            log.debug("Discovery socket error: %s", exc)

    transport.close()

    # Recreate with our protocol subclass
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock2.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock2.setblocking(False)
    sock2.bind(("", 0))

    transport2, _ = await loop.create_datagram_endpoint(
        _Protocol,
        sock=sock2,
    )

    try:
        transport2.sendto(PROBE, (BCAST_ADDR, DISCOVERY_PORT))
        try:
            transport2.sendto(PROBE, (MCAST_ADDR, DISCOVERY_PORT))
        except Exception:
            pass  # multicast may fail on some networks, broadcast is enough
        await asyncio.sleep(timeout)
    finally:
        transport2.close()

    return list(found.values())


async def scan_led_panels(timeout: float = SCAN_TIMEOUT) -> list[DiscoveredDevice]:
    """Scan and return only ULED-AT LED panels."""
    all_devices = await scan(timeout)
    return [d for d in all_devices if d.platform in LED_PLATFORMS]
