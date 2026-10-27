"""Diagnostics for WiseMirror."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_BLE_ADDRESS, CONF_MAC
from .coordinator import WiseMirrorConfigEntry

TO_REDACT = {
    CONF_HOST,
    CONF_MAC,
    CONF_BLE_ADDRESS,
    "unique_id",
    "location",
    "info",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WiseMirrorConfigEntry
) -> dict[str, Any]:
    """Return the stored identity and the last decoded state, redacted."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "model": coordinator.model,
        "firmware": coordinator.firmware,
        "last_update_success": coordinator.last_update_success,
        "weather_source_options": coordinator.weather_source_options,
        "state": async_redact_data(_jsonable(asdict(data)), TO_REDACT)
        if data
        else None,
    }
