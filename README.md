# Myles' Home Assistant Add-ons

A small collection of Home Assistant add-ons.

## Installation

Add this repository to Home Assistant:

1. Open Home Assistant.
2. Settings → Add-ons → Add-on Store.
3. Click the three-dot menu (top right) → Repositories.
4. Paste: `https://github.com/m7les/hassio-addons`
5. Click "Add", then close the dialog.
6. Refresh the Add-on Store. The new add-ons will appear under a new section.

Or click here:

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fm7les%2Fhassio-addons)

## Add-ons

### [LIFX Switch Bridge](./lifx_switch_bridge)

Exposes LIFX Switch relays and backlights to Home Assistant via the Photons Interactor + MQTT discovery.
The native LIFX integration doesn't support LIFX Switch devices, and HomeKit Controller is broken on switches with a failed Matter/HomeKit firmware update. This bridge fills that gap.

## License

MIT. See [LICENSE](./LICENSE).
