"""Button: send Home Assistant's home location to the mirror."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorEntity
from .helpers import async_send_location

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities([WiseMirrorHomeLocationButton(entry.runtime_data)])


class WiseMirrorHomeLocationButton(WiseMirrorEntity, ButtonEntity):
    """Set the weather location to the home zone (empty AccuWeather key)."""

    _attr_translation_key = "use_home_location"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator, "use_home_location")

    async def async_press(self) -> None:
        """Send the home location."""
        await async_send_location(self.hass, self.coordinator)
