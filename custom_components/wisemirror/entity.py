"""Base entity for WiseMirror."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WiseMirrorCoordinator


class WiseMirrorEntity(CoordinatorEntity[WiseMirrorCoordinator]):
    """Common device info and unique ID."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WiseMirrorCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.identifier}_{key}"
        connections: set[tuple[str, str]] = set()
        if coordinator.mac:
            connections.add((CONNECTION_NETWORK_MAC, coordinator.mac))
        if coordinator.ble_address:
            connections.add((CONNECTION_BLUETOOTH, coordinator.ble_address))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.identifier)},
            connections=connections,
            manufacturer=MANUFACTURER,
            model=coordinator.model,
            name=coordinator.config_entry.title,
            sw_version=coordinator.firmware,
        )


class WiseMirrorSettingsEntity(WiseMirrorEntity):
    """Entity whose state comes from the settings block."""

    @property
    def available(self) -> bool:
        """Available once settings have been read."""
        return super().available and self.coordinator.data.settings is not None


class WiseMirrorWeatherEntity(WiseMirrorEntity):
    """Entity whose state comes from the weather block."""

    @property
    def available(self) -> bool:
        """Available once the weather block has been read."""
        return super().available and self.coordinator.data.weather is not None
