"""Shared helpers."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import FIRST_CONTACT_TRIES
from .coordinator import WiseMirrorCoordinator
from .protocol import (
    NameReply,
    build_location,
    build_name,
    format_location,
    parse_name_reply,
)
from .transport import WiseMirrorError, WiseMirrorUdpClient

_LOGGER = logging.getLogger(__name__)


def home_location(
    hass: HomeAssistant,
    city: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    location_key: str = "",
) -> str:
    """Location string, filling gaps from the home zone."""
    return format_location(
        city or hass.config.location_name or "Home",
        hass.config.latitude if latitude is None else latitude,
        hass.config.longitude if longitude is None else longitude,
        location_key,
    )


async def async_send_location(
    hass: HomeAssistant,
    coordinator: WiseMirrorCoordinator,
    city: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    location_key: str = "",
) -> str:
    """Send 0x0B with the given location (default: the home zone)."""
    return await coordinator.async_set_location(
        city or hass.config.location_name or "Home",
        hass.config.latitude if latitude is None else latitude,
        hass.config.longitude if longitude is None else longitude,
        location_key,
    )


async def async_first_contact(
    host: str, name: str, location: str | None
) -> NameReply | None:
    """First contact after Wi-Fi setup.

    Send the name (0x0F) until the mirror answers, up to 20 tries, then the
    location (0x0B). Returns the firmware carried in the name reply.
    """
    client = WiseMirrorUdpClient(host)
    reply = await client.request(build_name(name), tries=FIRST_CONTACT_TRIES)
    if location:
        try:
            await client.request(build_location(location))
        except WiseMirrorError as err:
            _LOGGER.warning("Could not send location to %s: %s", host, err)
    return parse_name_reply(reply)
