"""Integration actions."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
import voluptuous as vol

from .ble import WiseMirrorBleClient
from .const import (
    DOMAIN,
    EVENT_FIRMWARE_PROGRESS,
    SERVICE_PROVISION_WIFI,
    SERVICE_SEND_COMMAND,
    SERVICE_SET_LOCATION,
    SERVICE_UPDATE_FIRMWARE,
)
from .coordinator import WiseMirrorCoordinator
from .firmware import async_update_firmware
from .helpers import async_send_location
from .protocol import AP_HOST, FirmwareProgress, encode_frame
from .transport import WiseMirrorError, async_provision_ap

_LOGGER = logging.getLogger(__name__)

ATTR_CITY = "city"
ATTR_NAME = "name"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_LOCATION_KEY = "location_key"
ATTR_COMMAND = "command"
ATTR_PAYLOAD = "payload"
ATTR_FILE = "file"
ATTR_METHOD = "method"
ATTR_SSID = "ssid"
ATTR_BLE_ADDRESS = "ble_address"

METHOD_AP = "access_point"
METHOD_BLUETOOTH = "bluetooth"

TARGET_FIELDS = {
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Optional(ATTR_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
}

SET_LOCATION_SCHEMA = vol.Schema(
    {
        **TARGET_FIELDS,
        vol.Optional(ATTR_CITY): cv.string,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_LATITUDE): cv.latitude,
        vol.Optional(ATTR_LONGITUDE): cv.longitude,
        vol.Optional(ATTR_LOCATION_KEY, default=""): cv.string,
    }
)

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        **TARGET_FIELDS,
        vol.Required(ATTR_COMMAND): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        vol.Optional(ATTR_PAYLOAD, default="00"): cv.string,
    }
)

UPDATE_FIRMWARE_SCHEMA = vol.Schema(
    {
        **TARGET_FIELDS,
        vol.Required(ATTR_FILE): cv.string,
    }
)

PROVISION_WIFI_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SSID): cv.string,
        vol.Optional(CONF_PASSWORD, default=""): cv.string,
        vol.Optional(ATTR_METHOD, default=METHOD_AP): vol.In(
            [METHOD_AP, METHOD_BLUETOOTH]
        ),
        vol.Optional(CONF_HOST, default=AP_HOST): cv.string,
        vol.Optional(ATTR_BLE_ADDRESS): cv.string,
    }
)


def _coordinators(call: ServiceCall) -> list[WiseMirrorCoordinator]:
    """Resolve device, entity and area targets to loaded mirrors."""
    hass = call.hass
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device_ids: set[str] = set(call.data.get(ATTR_DEVICE_ID, []))
    for entity_id in call.data.get(ATTR_ENTITY_ID, []):
        if (entity := ent_reg.async_get(entity_id)) and entity.device_id:
            device_ids.add(entity.device_id)
    for area_id in call.data.get(ATTR_AREA_ID, []):
        device_ids.update(d.id for d in dr.async_entries_for_area(dev_reg, area_id))
    coordinators: dict[str, WiseMirrorCoordinator] = {}
    for device_id in device_ids:
        if (device := dev_reg.async_get(device_id)) is None:
            continue
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if (
                entry is not None
                and entry.domain == DOMAIN
                and entry.state is ConfigEntryState.LOADED
            ):
                coordinators[entry.entry_id] = entry.runtime_data
    if not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_target_mirror"
        )
    return list(coordinators.values())


def _single_coordinator(call: ServiceCall) -> WiseMirrorCoordinator:
    coordinators = _coordinators(call)
    if len(coordinators) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="one_mirror_only"
        )
    return coordinators[0]


def _parse_hex(value: str) -> bytes:
    try:
        return bytes.fromhex(value.replace(":", " ").replace(",", " "))
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_payload",
            translation_placeholders={"payload": value},
        ) from err


async def _async_set_location(call: ServiceCall) -> ServiceResponse:
    locations: dict[str, str] = {}
    for coordinator in _coordinators(call):
        locations[coordinator.config_entry.title] = await async_send_location(
            call.hass,
            coordinator,
            call.data.get(ATTR_CITY) or call.data.get(ATTR_NAME),
            call.data.get(ATTR_LATITUDE),
            call.data.get(ATTR_LONGITUDE),
            call.data[ATTR_LOCATION_KEY],
        )
    if len(locations) == 1:
        return {"location": next(iter(locations.values()))}
    return {"locations": locations}


async def _async_send_command(call: ServiceCall) -> ServiceResponse:
    coordinator = _single_coordinator(call)
    frame = encode_frame(call.data[ATTR_COMMAND], _parse_hex(call.data[ATTR_PAYLOAD]))
    reply = await coordinator.async_send(frame)
    return {
        "request": frame.hex(" "),
        "reply": reply.hex(" ") if reply is not None else None,
    }


async def _async_update_firmware(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _single_coordinator(call)
    if not coordinator.host:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_address"
        )
    path = call.data[ATTR_FILE]
    if not hass.config.is_allowed_path(path):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="path_not_allowed",
            translation_placeholders={"path": path},
        )
    image = await hass.async_add_executor_job(Path(path).read_bytes)
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, coordinator.identifier)}
    )
    device_id = device.id if device else None

    @callback
    def _progress(progress: FirmwareProgress) -> None:
        _LOGGER.info(
            "%s firmware update: %s%%", coordinator.config_entry.title, progress.percent
        )
        hass.bus.async_fire(
            EVENT_FIRMWARE_PROGRESS,
            {ATTR_DEVICE_ID: device_id, "progress": progress.percent},
        )

    try:
        result = await async_update_firmware(
            coordinator.host, image, coordinator.model, _progress
        )
    except (WiseMirrorError, OSError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="firmware_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    await coordinator.async_request_refresh()
    return {"progress": result.percent}


async def _async_provision_wifi(call: ServiceCall) -> None:
    ssid = call.data[ATTR_SSID]
    password = call.data[CONF_PASSWORD]
    try:
        if call.data[ATTR_METHOD] == METHOD_AP:
            await async_provision_ap(ssid, password, call.data[CONF_HOST])
            return
        address = call.data.get(ATTR_BLE_ADDRESS)
        if not address:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="ble_address_required"
            )
        hass = call.hass

        def _device():
            return bluetooth.async_ble_device_from_address(
                hass, address, connectable=True
            )

        await WiseMirrorBleClient(_device, address).send_wifi_credentials(
            ssid, password
        )
    except WiseMirrorError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="provision_failed",
            translation_placeholders={"error": str(err)},
        ) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LOCATION,
        _async_set_location,
        schema=SET_LOCATION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _async_send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_FIRMWARE,
        _async_update_firmware,
        schema=UPDATE_FIRMWARE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PROVISION_WIFI,
        _async_provision_wifi,
        schema=PROVISION_WIFI_SCHEMA,
    )
