"""Switches for the mirror's on/off settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MirrorData, WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorSettingsEntity, WiseMirrorWeatherEntity
from .protocol import (
    SETTINGS_FLAG_24H,
    SETTINGS_FLAG_DAY_FIRST,
    SETTINGS_FLAG_KEY_TONE,
    WEATHER_FLAG_TODAY,
    WEATHER_FLAG_TWO_DAY,
    Settings,
    build_clock_24h,
    build_day_first,
    build_key_tone,
    build_sleep_mode,
    build_weather,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SettingsSwitchDescription(SwitchEntityDescription):
    """A switch backed by the settings block."""

    value_fn: Callable[[Settings], bool]
    build_fn: Callable[[bool], bytes]
    apply_fn: Callable[[Settings, bool], Settings]


SETTINGS_SWITCHES: tuple[SettingsSwitchDescription, ...] = (
    SettingsSwitchDescription(
        key="key_tone",
        translation_key="key_tone",
        entity_category=EntityCategory.CONFIG,
        # Not every model has buttons that beep.
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.key_tone,
        build_fn=build_key_tone,
        apply_fn=lambda s, on: s.with_flag(SETTINGS_FLAG_KEY_TONE, on),
    ),
    SettingsSwitchDescription(
        key="clock_24h",
        translation_key="clock_24h",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.clock_24h,
        build_fn=build_clock_24h,
        apply_fn=lambda s, on: s.with_flag(SETTINGS_FLAG_24H, on),
    ),
    SettingsSwitchDescription(
        key="day_first",
        translation_key="day_first",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.day_first,
        build_fn=build_day_first,
        apply_fn=lambda s, on: s.with_flag(SETTINGS_FLAG_DAY_FIRST, on),
    ),
    SettingsSwitchDescription(
        key="sleep_mode",
        translation_key="sleep_mode",
        value_fn=lambda s: s.sleep_on,
        build_fn=build_sleep_mode,
        apply_fn=lambda s, on: replace(s, sleep_on=on),
    ),
)


@dataclass(frozen=True, kw_only=True)
class WeatherSwitchDescription(SwitchEntityDescription):
    """A switch backed by one weather flag bit."""

    bit: int


WEATHER_SWITCHES: tuple[WeatherSwitchDescription, ...] = (
    WeatherSwitchDescription(
        key="two_day_weather",
        translation_key="two_day_weather",
        entity_category=EntityCategory.CONFIG,
        bit=WEATHER_FLAG_TWO_DAY,
    ),
    # Rarely changed; written back as read when other weather settings change.
    WeatherSwitchDescription(
        key="today_weather",
        translation_key="today_weather",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        bit=WEATHER_FLAG_TODAY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        WiseMirrorSettingsSwitch(coordinator, desc) for desc in SETTINGS_SWITCHES
    ]
    entities.extend(
        WiseMirrorWeatherSwitch(coordinator, desc) for desc in WEATHER_SWITCHES
    )
    async_add_entities(entities)


class WiseMirrorSettingsSwitch(WiseMirrorSettingsEntity, SwitchEntity):
    """Switch for one settings flag."""

    entity_description: SettingsSwitchDescription

    def __init__(
        self, coordinator: WiseMirrorCoordinator, description: SettingsSwitchDescription
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the setting."""
        return self.entity_description.value_fn(self.coordinator.data.settings)

    async def _async_set(self, on: bool) -> None:
        settings = self.coordinator.data.settings
        await self.coordinator.async_send(
            self.entity_description.build_fn(on),
            settings=self.entity_description.apply_fn(settings, on)
            if settings
            else None,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._async_set(False)


class WiseMirrorWeatherSwitch(WiseMirrorWeatherEntity, SwitchEntity):
    """Switch for one weather flag; 0x30 always carries flags and source."""

    entity_description: WeatherSwitchDescription

    def __init__(
        self, coordinator: WiseMirrorCoordinator, description: WeatherSwitchDescription
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the flag."""
        return bool(self.coordinator.data.weather.flags & self.entity_description.bit)

    async def _async_set(self, on: bool) -> None:
        bit = self.entity_description.bit

        def _build(data: MirrorData):
            weather = data.weather
            flags = weather.flags | bit if on else weather.flags & ~bit
            return (
                build_weather(flags, weather.source),
                None,
                replace(weather, flags=flags),
            )

        await self.coordinator.async_write(_build)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._async_set(False)
