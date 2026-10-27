"""Background discovery: a periodic broadcast of the discovery query.

New mirrors are offered as discovered config flows. Configured mirrors that
answer from a new address have their entry updated.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

from .const import CONF_FIRMWARE, CONF_MAC, CONF_MODEL, DISCOVERY_SCAN_INTERVAL, DOMAIN
from .protocol import DiscoveryReply
from .transport import async_discover

_LOGGER = logging.getLogger(__name__)


def discovery_data(reply: DiscoveryReply) -> dict[str, str | None]:
    """Flow data for one reply."""
    return {
        CONF_HOST: reply.host,
        CONF_MAC: reply.mac,
        CONF_MODEL: reply.model,
        CONF_FIRMWARE: reply.version,
    }


@callback
def async_handle_replies(hass: HomeAssistant, replies: list[DiscoveryReply]) -> None:
    """Update known mirrors and offer unknown ones."""
    all_entries = hass.config_entries.async_entries(DOMAIN)
    entries = {entry.unique_id: entry for entry in all_entries}
    hosts = {entry.data.get(CONF_HOST) for entry in all_entries}
    for reply in replies:
        entry = entries.get(reply.mac)
        if entry is None and reply.host in hosts:
            # Configured by IP before its MAC could be read.
            continue
        if entry is None:
            discovery_flow.async_create_flow(
                hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=discovery_data(reply),
            )
        elif entry.state is ConfigEntryState.LOADED:
            entry.runtime_data.async_update_host(reply.host, reply.version)
        elif entry.data.get(CONF_HOST) != reply.host:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_HOST: reply.host}
            )


async def async_scan(hass: HomeAssistant) -> None:
    """Broadcast once and handle the replies."""
    try:
        replies = await async_discover()
    except OSError as err:
        _LOGGER.debug("Discovery broadcast failed: %s", err)
        return
    async_handle_replies(hass, replies)


@callback
def async_start_discovery(hass: HomeAssistant) -> None:
    """Scan once Home Assistant has started, then every 15 minutes."""

    async def _scan(*_: object) -> None:
        await async_scan(hass)

    async_at_started(hass, _scan)
    async_track_time_interval(
        hass, _scan, timedelta(seconds=DISCOVERY_SCAN_INTERVAL), cancel_on_shutdown=True
    )
