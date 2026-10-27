"""Constants for the WiseMirror integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "wisemirror"
MANUFACTURER: Final = "Adsmart Baolun"

CONF_MAC: Final = "mac"
CONF_MODEL: Final = "model"
CONF_FIRMWARE: Final = "firmware"
CONF_BLE_ADDRESS: Final = "ble_address"
CONF_HEARTBEAT_INTERVAL: Final = "heartbeat_interval"
CONF_SETTINGS_INTERVAL: Final = "settings_interval"
CONF_FOLLOW_HOME_LOCATION: Final = "use_ha_location"
CONF_APPLY_TO_ALL: Final = "apply_to_all"

DEFAULT_HEARTBEAT_INTERVAL: Final = 30
DEFAULT_SETTINGS_INTERVAL: Final = 300
DEFAULT_FOLLOW_HOME_LOCATION: Final = False
MIN_HEARTBEAT_INTERVAL: Final = 5
MAX_HEARTBEAT_INTERVAL: Final = 3600
MIN_SETTINGS_INTERVAL: Final = 30
MAX_SETTINGS_INTERVAL: Final = 3600

# Unicast discovery query used when a mirror does not answer the heartbeat.
PROBE_TIMEOUT: Final = 1.0
# Background broadcast that offers new mirrors and follows IP changes.
DISCOVERY_SCAN_INTERVAL: Final = 900

# A mirror counts as offline after this many missed heartbeats.
MAX_MISSED_HEARTBEATS: Final = 4
# An advertisement older than this no longer counts as "reachable over BLE".
BLE_STALE_SECONDS: Final = 300

TRANSPORT_WIFI: Final = "wifi"
TRANSPORT_BLUETOOTH: Final = "bluetooth"

# Tries for the first-contact name write after Wi-Fi setup.
FIRST_CONTACT_TRIES: Final = 20
# How long to look for a freshly provisioned mirror on the LAN.
PROVISION_DISCOVERY_SECONDS: Final = 90

EVENT_FIRMWARE_PROGRESS: Final = f"{DOMAIN}_firmware_progress"

SERVICE_SET_LOCATION: Final = "set_location"
SERVICE_SEND_COMMAND: Final = "send_command"
SERVICE_UPDATE_FIRMWARE: Final = "update_firmware"
SERVICE_PROVISION_WIFI: Final = "provision_wifi"
