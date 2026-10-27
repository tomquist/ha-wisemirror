"""Selects: temperature unit, weekday language, brightness, weather source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MirrorData, WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorSettingsEntity, WiseMirrorWeatherEntity
from .protocol import (
    BRIGHTNESS_PRESETS,
    SETTINGS_FLAG_CELSIUS,
    SETTINGS_FLAG_CHINESE,
    SLEEP_BRIGHTNESS_PRESETS,
    WEATHER_SOURCES,
    Settings,
    build_brightness,
    build_sleep_brightness,
    build_temp_unit,
    build_weather,
    build_weekday_language,
    weather_source_key,
)

PARALLEL_UPDATES = 1


def _nearest_preset(presets: dict[str, int], value: int) -> str:
    return min(presets, key=lambda key: abs(presets[key] - value))


@dataclass(frozen=True, kw_only=True)
class SettingsSelectDescription(SelectEntityDescription):
    """A select backed by the settings block."""

    value_fn: Callable[[Settings], str | None]
    build_fn: Callable[[str], bytes]
    apply_fn: Callable[[Settings, str], Settings]


SETTINGS_SELECTS: tuple[SettingsSelectDescription, ...] = (
    SettingsSelectDescription(
        key="temperature_unit",
        translation_key="temperature_unit",
        entity_category=EntityCategory.CONFIG,
        options=["celsius", "fahrenheit"],
        value_fn=lambda s: "celsius" if s.celsius else "fahrenheit",
        build_fn=lambda option: build_temp_unit(option == "celsius"),
        apply_fn=lambda s, option: s.with_flag(
            SETTINGS_FLAG_CELSIUS, option == "celsius"
        ),
    ),
    SettingsSelectDescription(
        key="weekday_language",
        translation_key="weekday_language",
        entity_category=EntityCategory.CONFIG,
        options=["english", "chinese"],
        value_fn=lambda s: "chinese" if s.chinese_weekday else "english",
        build_fn=lambda option: build_weekday_language(option == "chinese"),
        apply_fn=lambda s, option: s.with_flag(
            SETTINGS_FLAG_CHINESE, option == "chinese"
        ),
    ),
    SettingsSelectDescription(
        key="brightness",
        translation_key="brightness_preset",
        options=list(BRIGHTNESS_PRESETS),
        value_fn=lambda s: _nearest_preset(BRIGHTNESS_PRESETS, s.brightness),
        build_fn=lambda option: build_brightness(BRIGHTNESS_PRESETS[option]),
        apply_fn=lambda s, option: replace(s, brightness=BRIGHTNESS_PRESETS[option]),
    ),
    SettingsSelectDescription(
        key="sleep_brightness",
        translation_key="sleep_brightness_preset",
        entity_category=EntityCategory.CONFIG,
        options=list(SLEEP_BRIGHTNESS_PRESETS),
        value_fn=lambda s: _nearest_preset(
            SLEEP_BRIGHTNESS_PRESETS, s.sleep_brightness
        ),
        build_fn=lambda option: build_sleep_brightness(
            SLEEP_BRIGHTNESS_PRESETS[option]
        ),
        apply_fn=lambda s, option: replace(
            s, sleep_brightness=SLEEP_BRIGHTNESS_PRESETS[option]
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up selects."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        WiseMirrorSettingsSelect(coordinator, desc) for desc in SETTINGS_SELECTS
    ]
    entities.append(WiseMirrorWeatherSourceSelect(coordinator))
    async_add_entities(entities)


class WiseMirrorSettingsSelect(WiseMirrorSettingsEntity, SelectEntity):
    """Select for one setting."""

    entity_description: SettingsSelectDescription

    def __init__(
        self, coordinator: WiseMirrorCoordinator, description: SettingsSelectDescription
    ) -> None:
        """Initialise the select."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self.entity_description.value_fn(self.coordinator.data.settings)

    async def async_select_option(self, option: str) -> None:
        """Write the option."""
        settings = self.coordinator.data.settings
        await self.coordinator.async_send(
            self.entity_description.build_fn(option),
            settings=self.entity_description.apply_fn(settings, option),
        )


class WiseMirrorWeatherSourceSelect(WiseMirrorWeatherEntity, SelectEntity):
    """Weather provider; the options depend on model and firmware."""

    _attr_translation_key = "weather_source"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, "weather_source")

    @property
    def options(self) -> list[str]:
        """Options for this mirror."""
        options = self.coordinator.weather_source_options
        current = self.current_option
        if current is not None and current not in options:
            options = [*options, current]
        return options

    @property
    def current_option(self) -> str | None:
        """Return the current source."""
        return weather_source_key(self.coordinator.data.weather.source)

    async def async_select_option(self, option: str) -> None:
        """Write the source, keeping the flags as last read."""
        source = WEATHER_SOURCES[option]

        def _build(data: MirrorData):
            weather = data.weather
            return (
                build_weather(weather.flags, source),
                None,
                replace(weather, source=source),
            )

        await self.coordinator.async_write(_build)
