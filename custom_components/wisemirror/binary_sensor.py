"""Binary sensors: connectivity and read-only settings flags."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorEntity, WiseMirrorSettingsEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [WiseMirrorConnectivity(coordinator), WiseMirrorAlwaysBright(coordinator)]
    )


class WiseMirrorConnectivity(WiseMirrorEntity, BinarySensorEntity):
    """Online/offline; stays available so automations can see "offline"."""

    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "connectivity")

    @property
    def available(self) -> bool:
        """Always available."""
        return True

    @property
    def is_on(self) -> bool:
        """True while polls reach the mirror (over Wi-Fi or Bluetooth)."""
        return self.coordinator.last_update_success


class WiseMirrorAlwaysBright(WiseMirrorSettingsEntity, BinarySensorEntity):
    """Settings flag bit 4 (read-only)."""

    _attr_translation_key = "always_bright"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "always_bright")

    @property
    def is_on(self) -> bool:
        """Return the flag."""
        return self.coordinator.data.settings.always_bright
