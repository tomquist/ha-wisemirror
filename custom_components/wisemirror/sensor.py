"""Sensors: temperature, humidity, firmware and read-only settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TRANSPORT_BLUETOOTH, TRANSPORT_WIFI
from .coordinator import MirrorData, WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorEntity, WiseMirrorSettingsEntity
from .protocol import parse_location

PARALLEL_UPDATES = 0


def _city(location: str) -> str | None:
    """City name from the location string, URL-decoded."""
    if parsed := parse_location(location):
        return parsed.city or None
    return unquote(location.split("(", 1)[0]) or None


@dataclass(frozen=True, kw_only=True)
class WiseMirrorSensorDescription(SensorEntityDescription):
    """Describes a WiseMirror sensor."""

    value_fn: Callable[[WiseMirrorCoordinator, MirrorData], Any]
    needs_settings: bool = False


SENSORS: tuple[WiseMirrorSensorDescription, ...] = (
    WiseMirrorSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator, data: data.firmware or coordinator.firmware,
    ),
    WiseMirrorSensorDescription(
        key="connection",
        translation_key="connection",
        device_class=SensorDeviceClass.ENUM,
        options=[TRANSPORT_WIFI, TRANSPORT_BLUETOOTH],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _c, data: data.transport,
    ),
    WiseMirrorSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _c, data: data.host,
    ),
    WiseMirrorSensorDescription(
        key="weather_location",
        translation_key="weather_location",
        entity_category=EntityCategory.DIAGNOSTIC,
        needs_settings=True,
        value_fn=lambda _c, data: _city(data.settings.location),
    ),
    WiseMirrorSensorDescription(
        key="brightness_duration",
        translation_key="brightness_duration",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        needs_settings=True,
        value_fn=lambda _c, data: data.settings.brightness_duration,
    ),
    WiseMirrorSensorDescription(
        key="time_date_mode",
        translation_key="time_date_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        needs_settings=True,
        value_fn=lambda _c, data: data.settings.time_date_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        WiseMirrorTemperatureSensor(coordinator),
        WiseMirrorHumiditySensor(coordinator),
    ]
    entities.extend(WiseMirrorSensor(coordinator, desc) for desc in SENSORS)
    entities.extend(WiseMirrorAlarmSensor(coordinator, index) for index in range(5))
    async_add_entities(entities)


class WiseMirrorSensor(WiseMirrorEntity, SensorEntity):
    """Generic sensor driven by a description."""

    entity_description: WiseMirrorSensorDescription

    def __init__(
        self,
        coordinator: WiseMirrorCoordinator,
        description: WiseMirrorSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Settings-based sensors need a settings read."""
        if (
            self.entity_description.needs_settings
            and self.coordinator.data.settings is None
        ):
            return False
        return super().available

    @property
    def native_value(self) -> Any:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)


class WiseMirrorTemperatureSensor(WiseMirrorEntity, SensorEntity):
    """Indoor temperature in the mirror's own unit."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "temperature")

    @property
    def native_value(self) -> int | None:
        """Whole degrees."""
        return self.coordinator.data.temperature

    @property
    def native_unit_of_measurement(self) -> str:
        """Unit reported with the value."""
        if self.coordinator.data.celsius is False:
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS


class WiseMirrorHumiditySensor(WiseMirrorEntity, SensorEntity):
    """Relative humidity; some models have no sensor and report 0."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: WiseMirrorCoordinator) -> None:
        """Initialise the sensor, disabled by default without a reading."""
        super().__init__(coordinator, "humidity")
        # Only applies when the entity is first registered.
        self._attr_entity_registry_enabled_default = (
            coordinator.data is None or coordinator.data.humidity is not None
        )

    @property
    def native_value(self) -> int | None:
        """Percent, or None when the mirror reports no reading."""
        return self.coordinator.data.humidity


class WiseMirrorAlarmSensor(WiseMirrorSettingsEntity, SensorEntity):
    """One of the five alarms (read-only)."""

    _attr_translation_key = "alarm"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: WiseMirrorCoordinator, index: int) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, f"alarm_{index + 1}")
        self._index = index
        self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def native_value(self) -> str | None:
        """``HH:MM`` when on, ``off`` otherwise."""
        alarm = self.coordinator.data.settings.alarms[self._index]
        if not alarm.enabled:
            return "off"
        return f"{alarm.hour:02d}:{alarm.minute:02d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Raw alarm fields."""
        alarm = self.coordinator.data.settings.alarms[self._index]
        return {
            "enabled": alarm.enabled,
            "hour": alarm.hour,
            "minute": alarm.minute,
            "weekday_mask": alarm.weekdays,
            "weekday_bits": alarm.weekday_list,
        }
