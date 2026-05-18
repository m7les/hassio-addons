# Changelog

## 0.1.5
- Fix relay switch entities briefly reverting in HA after a command. The command handler now polls the relay immediately after `SetRPower` so HA receives the confirmed state without waiting for the next poll cycle.

## 0.1.4
- Add `text` entity per switch to rename the device label from HA (`SetLabel`)
- Add `number` entity per switch for haptic feedback duration (0–65535 ms)
- Add `sensor` entities for WiFi RSSI (dBm) and firmware version (diagnostic)
- Per-device availability: each switch has its own availability topic; entities go unavailable independently when a switch loses power or WiFi
- Periodic re-discovery: new switches are picked up automatically without restarting the add-on (`rediscovery_interval` option, default 300 s)
- MQTT topic prefix is now configurable (`mqtt_topic_prefix` option, default `lifx_switch`)
- Backlight and haptic state now synced from device on each poll cycle; cache initialised from device rather than first command

## 0.1.3 (unreleased)
- Internal refactor included in 0.1.4

## 0.1.2
- Switch to host network mode

## 0.1.1
- Version bump

## 0.1.0
- Initial release: relay switch entities, on/off backlight light entities, MQTT discovery via Photons Interactor
