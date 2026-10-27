"""Text entities: device name and weather location."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorEntity, WiseMirrorSettingsEntity
from .protocol import build_location, parse_location

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up text entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [WiseMirrorNameText(coordinator), WiseMirrorLocationText(coordinator)]
    )


class WiseMirrorNameText(WiseMirrorEntity, TextEntity):
    """Name the mirror reports in its heartbeat JSON (0x0F to set)."""

    _attr_translation_key = "device_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 255

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "device_name")

    @property
    def native_value(self) -> str | None:
        """Return the name."""
        return self.coordinator.device_name or self.coordinator.config_entry.title

    async def async_set_value(self, value: str) -> None:
        """Send the name."""
        await self.coordinator.async_set_name(value)


class WiseMirrorLocationText(WiseMirrorSettingsEntity, TextEntity):
    """Raw location string, ``<URL-encoded city>(<lat>,<lon>)(<key>)``."""

    _attr_translation_key = "location"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 255

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "location")

    @property
    def native_value(self) -> str | None:
        """Return the stored location string."""
        return self.coordinator.data.settings.location

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Decoded city, coordinates and AccuWeather key."""
        location = parse_location(self.coordinator.data.settings.location)
        if location is None:
            return None
        return {
            "city": location.city,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "location_key": location.location_key,
        }

    async def async_set_value(self, value: str) -> None:
        """Send the location string as entered."""
        settings = self.coordinator.data.settings
        await self.coordinator.async_send(
            build_location(value), settings=replace(settings, location=value)
        )
