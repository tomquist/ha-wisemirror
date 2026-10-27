"""Numbers: raw display and sleep brightness (0-100)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorSettingsEntity
from .protocol import Settings, build_brightness, build_sleep_brightness

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BrightnessDescription(NumberEntityDescription):
    """A raw brightness byte.

    Besides the Low/Medium/High presets, tested hardware takes any value from
    0 to 100, including levels below the lowest preset.
    """

    value_fn: Callable[[Settings], int]
    build_fn: Callable[[int], bytes]
    apply_fn: Callable[[Settings, int], Settings]


NUMBERS: tuple[BrightnessDescription, ...] = (
    BrightnessDescription(
        key="brightness_percent",
        translation_key="brightness_percent",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.brightness,
        build_fn=build_brightness,
        apply_fn=lambda s, value: replace(s, brightness=value),
    ),
    BrightnessDescription(
        key="sleep_brightness_percent",
        translation_key="sleep_brightness_percent",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.sleep_brightness,
        build_fn=build_sleep_brightness,
        apply_fn=lambda s, value: replace(s, sleep_brightness=value),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up numbers."""
    coordinator = entry.runtime_data
    async_add_entities(WiseMirrorBrightness(coordinator, desc) for desc in NUMBERS)


class WiseMirrorBrightness(WiseMirrorSettingsEntity, NumberEntity):
    """Raw brightness level."""

    entity_description: BrightnessDescription

    def __init__(
        self, coordinator: WiseMirrorCoordinator, description: BrightnessDescription
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        """Return the level."""
        return self.entity_description.value_fn(self.coordinator.data.settings)

    async def async_set_native_value(self, value: float) -> None:
        """Write the level."""
        level = max(0, min(100, round(value)))
        settings = self.coordinator.data.settings
        await self.coordinator.async_send(
            self.entity_description.build_fn(level),
            settings=self.entity_description.apply_fn(settings, level),
        )
