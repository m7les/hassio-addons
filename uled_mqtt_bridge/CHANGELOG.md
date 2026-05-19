# Changelog

## 0.3.2
- Fix reset leaving device as "adopted_elsewhere": unadopt now also clears the controller UUID (`set-led-ctrler-uid ""`), so the device reports as fully unadopted in the next discovery scan

## 0.3.1
- Reset button on each adopted light's device card: SSHes into the device, clears adoption state, removes HA entities, cancels the bridge — device reappears as unadopted in the next discovery scan
- Bridge tasks moved outside the infrastructure TaskGroup so individual bridges can be cancelled on reset without taking down the whole add-on
- Catch-all exception handler in bridge run loop so unexpected errors are logged rather than silently dropped

## 0.3.0
- IP healing: discovery scan now detects when an adopted device's IP changes (DHCP renewal) and updates the stored address and active bridge automatically — no manual intervention needed
- Exponential backoff on reconnect: unreachable devices back off from 10s to 120s instead of retrying every 10s forever; reverts to fast retry after any successful connection
- Empty `lights: []` default — no dummy entry to delete when relying entirely on discovery/adoption
- Pin Alpine base image to 3.20 in `build.yaml` for reproducible builds
- Add `DOCS.md` for HA add-on store help page; update `README.md` to cover discovery/adoption flow

## 0.2.4
- Device does not respond to set commands — replaced dead `v2/rsp/#` handler with a post-command status poll (500ms after last queued command) for near-immediate confirmed state updates

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
