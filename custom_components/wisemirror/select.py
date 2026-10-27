"""Selects: units, language, brightness, weather source and weather location."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    EVENT_STATE_CHANGED,
    EntityCategory,
)
from homeassistant.core import Event, HomeAssistant, State, callback
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
    parse_location,
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
    entities.append(WiseMirrorWeatherLocationSelect(coordinator))
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


# Stored coordinates are rounded to two decimals (about 1 km).
_SAME_PLACE = 0.006


class WiseMirrorWeatherLocationSelect(WiseMirrorSettingsEntity, SelectEntity):
    """Pick the place the mirror shows the weather forecast for.

    The options are Home Assistant's zones; choosing one sends its name and
    coordinates. A location that matches no zone (for example one set on the
    mirror before) is offered under its own name and keeps working.
    """

    _attr_translation_key = "weather_location"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, "weather_location")

    async def async_added_to_hass(self) -> None:
        """Follow zones being added, moved or renamed."""
        await super().async_added_to_hass()

        @callback
        def _zone_changed(event: Event) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED,
                _zone_changed,
                event_filter=callback(
                    lambda data: data["entity_id"].startswith("zone.")
                ),
            )
        )

    def _zones(self) -> dict[str, State]:
        """Zones by display name, Home first."""
        zones: dict[str, State] = {}
        states = sorted(
            self.hass.states.async_all("zone"),
            key=lambda state: (state.entity_id != "zone.home", state.name),
        )
        for state in states:
            if (
                state.attributes.get(ATTR_LATITUDE) is None
                or state.attributes.get(ATTR_LONGITUDE) is None
            ):
                continue
            name = state.attributes.get(ATTR_FRIENDLY_NAME) or state.entity_id
            zones.setdefault(name, state)
        return zones

    def _current(self) -> tuple[str | None, bool]:
        """Return (option, matches a zone) for the stored location."""
        location = parse_location(self.coordinator.data.settings.location)
        if location is None:
            return None, False
        for name, state in self._zones().items():
            if (
                abs(state.attributes[ATTR_LATITUDE] - location.latitude) <= _SAME_PLACE
                and abs(state.attributes[ATTR_LONGITUDE] - location.longitude)
                <= _SAME_PLACE
            ):
                return name, True
        return location.city or f"{location.latitude}, {location.longitude}", False

    @property
    def options(self) -> list[str]:
        """Zones, plus the current location if it matches none."""
        options = list(self._zones())
        current, is_zone = self._current()
        if current is not None and not is_zone:
            options.append(current if current not in options else f"{current} *")
        return options

    @property
    def current_option(self) -> str | None:
        """The zone (or custom location) the mirror uses."""
        current, is_zone = self._current()
        if current is not None and not is_zone and current in self._zones():
            return f"{current} *"
        return current

    async def async_select_option(self, option: str) -> None:
        """Send the zone's name and coordinates."""
        state = self._zones().get(option)
        if state is None:
            return  # the current custom location: nothing to change
        await self.coordinator.async_set_location(
            state.name,
            state.attributes[ATTR_LATITUDE],
            state.attributes[ATTR_LONGITUDE],
        )
