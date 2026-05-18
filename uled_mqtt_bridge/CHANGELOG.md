# Changelog

## 0.2.2
- Fix brightness commands: device uses 0–100 scale, HA uses 0–255 — values were passed through unscaled causing the device to wrap (e.g. 138 → 37, 255 → ~53)
- Fix HA retrying commands: after sending any command, immediately publish optimistic state to HA so it stops resending before the next 30s poll

## 0.2.1
- Fix adopt button silently doing nothing: `discovered` dict was keyed by colon-separated MAC (`74:83:c2:52:78:5e`) but the adopt topic uses the colon-free form (`7483c252785e`), so every adopt request missed the lookup

## 0.2.0
- Network discovery via Ubiquiti UDP protocol (port 10001): unadopted ULED-AT panels found on the LAN are surfaced in HA as a device card with a status sensor and an Adopt button
- SSH adoption (ubnt/ubnt): pressing Adopt SSHes into the device, marks it as managed, and immediately starts controlling it — light entity appears in HA without a restart
- Adopted devices persisted to `/data/adopted.json` and restored on startup
- Single MQTT message loop handles both light commands and adopt button presses via wildcard subscription

## 0.1.0
- Initial release: direct MQTT bridge to ULED-AT panels (bypasses eot-controller), HA MQTT Discovery for dimmable light entities, status polling, reconnection handling
