"""Time entities for the sleep window."""

from __future__ import annotations

from dataclasses import replace
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MirrorData, WiseMirrorConfigEntry, WiseMirrorCoordinator
from .entity import WiseMirrorSettingsEntity
from .protocol import build_sleep_window

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WiseMirrorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sleep start and end."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            WiseMirrorSleepTime(coordinator, start=True),
            WiseMirrorSleepTime(coordinator, start=False),
        ]
    )


class WiseMirrorSleepTime(WiseMirrorSettingsEntity, TimeEntity):
    """Start or end of the sleep window; 0x0D always carries both."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: WiseMirrorCoordinator, *, start: bool) -> None:
        """Initialise the entity."""
        key = "sleep_start" if start else "sleep_end"
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._start = start

    @property
    def native_value(self) -> time | None:
        """Return the time."""
        s = self.coordinator.data.settings
        hour, minute = (
            (s.sleep_start_hour, s.sleep_start_minute)
            if self._start
            else (s.sleep_end_hour, s.sleep_end_minute)
        )
        if hour > 23 or minute > 59:
            return None
        return time(hour, minute)

    async def async_set_value(self, value: time) -> None:
        """Write the window, keeping the other end as last read."""
        start = self._start

        def _build(data: MirrorData):
            s = data.settings
            if start:
                new = replace(
                    s, sleep_start_hour=value.hour, sleep_start_minute=value.minute
                )
            else:
                new = replace(
                    s, sleep_end_hour=value.hour, sleep_end_minute=value.minute
                )
            frame = build_sleep_window(
                min(new.sleep_start_hour, 23),
                min(new.sleep_start_minute, 59),
                min(new.sleep_end_hour, 23),
                min(new.sleep_end_minute, 59),
            )
            return frame, new, None

        await self.coordinator.async_write(_build)
