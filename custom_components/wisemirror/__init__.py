"""The WiseMirror integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_FOLLOW_HOME_LOCATION, DEFAULT_FOLLOW_HOME_LOCATION, DOMAIN
from .coordinator import WiseMirrorConfigEntry, WiseMirrorCoordinator
from .discovery import async_start_discovery
from .protocol import BLE_NAME_PREFIX, BLE_NAME_SHORT
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions and start the background discovery scan."""
    async_setup_services(hass)
    async_start_discovery(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WiseMirrorConfigEntry) -> bool:
    """Set up a mirror."""
    coordinator = WiseMirrorCoordinator(hass, entry)

    if coordinator.has_bluetooth and "bluetooth" in hass.config.components:
        for service_info in bluetooth.async_discovered_service_info(
            hass, connectable=False
        ):
            coordinator.async_handle_bluetooth(
                service_info, bluetooth.BluetoothChange.ADVERTISEMENT
            )
        for pattern in (f"{BLE_NAME_PREFIX}*", BLE_NAME_SHORT):
            entry.async_on_unload(
                bluetooth.async_register_callback(
                    hass,
                    coordinator.async_handle_bluetooth,
                    bluetooth.BluetoothCallbackMatcher(
                        local_name=pattern, connectable=False
                    ),
                    bluetooth.BluetoothScanningMode.PASSIVE,
                )
            )

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _sync_firmware() -> None:
        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, coordinator.identifier)}
        )
        if (
            device is not None
            and coordinator.firmware
            and device.sw_version != coordinator.firmware
        ):
            dr.async_get(hass).async_update_device(
                device.id, sw_version=coordinator.firmware
            )

    entry.async_on_unload(coordinator.async_add_listener(_sync_firmware))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when the options change, not when only the data does (a new IP
    # address or Bluetooth address is written into the entry while running).
    options = dict(entry.options)

    async def _async_entry_updated(
        hass: HomeAssistant, entry: WiseMirrorConfigEntry
    ) -> None:
        if dict(entry.options) != options:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    if entry.options.get(CONF_FOLLOW_HOME_LOCATION, DEFAULT_FOLLOW_HOME_LOCATION):
        await coordinator.async_sync_home_location()

        async def _on_core_config(_event: Event) -> None:
            await coordinator.async_sync_home_location()

        entry.async_on_unload(
            hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, _on_core_config)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: WiseMirrorConfigEntry) -> bool:
    """Unload a mirror."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
