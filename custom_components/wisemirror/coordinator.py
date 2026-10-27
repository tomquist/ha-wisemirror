"""Polling coordinator for one mirror."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
import logging
import time

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble import WiseMirrorBleClient
from .const import (
    BLE_STALE_SECONDS,
    CONF_BLE_ADDRESS,
    CONF_FIRMWARE,
    CONF_HEARTBEAT_INTERVAL,
    CONF_MAC,
    CONF_MODEL,
    CONF_SETTINGS_INTERVAL,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_SETTINGS_INTERVAL,
    DOMAIN,
    MAX_MISSED_HEARTBEATS,
    PROBE_TIMEOUT,
    TRANSPORT_BLUETOOTH,
    TRANSPORT_WIFI,
)
from .protocol import (
    UDP_ONLY_COMMANDS,
    Advertisement,
    Command,
    DiscoveryReply,
    Heartbeat,
    ProtocolError,
    Settings,
    WeatherSettings,
    build_location,
    build_name,
    find_advertisement,
    format_location,
    parse_location,
    parse_name_reply,
    version_from_string,
    weather_source_options,
)
from .transport import (
    WiseMirrorError,
    WiseMirrorRejected,
    WiseMirrorUdpClient,
    async_discover,
    async_find_mirror,
)

_LOGGER = logging.getLogger(__name__)

type WiseMirrorConfigEntry = ConfigEntry[WiseMirrorCoordinator]

# A write: (frame, expected settings, expected weather), built from the
# current state while the write lock is held.
type WriteBuilder = Callable[
    [MirrorData], tuple[bytes, Settings | None, WeatherSettings | None]
]


@dataclass(slots=True)
class MirrorData:
    """Everything the entities read."""

    heartbeat: Heartbeat | None = None
    probe: DiscoveryReply | None = None
    settings: Settings | None = None
    weather: WeatherSettings | None = None
    advertisement: Advertisement | None = None
    transport: str | None = None
    host: str | None = None
    temperature: int | None = None
    celsius: bool | None = None
    humidity: int | None = None
    firmware: str | None = None


class WiseMirrorCoordinator(DataUpdateCoordinator[MirrorData]):
    """Heartbeat every 30 s, settings every 5 min and after each write.

    A mirror that does not answer the heartbeat is polled with the discovery
    query on port 8000 instead, which also carries temperature, humidity and
    firmware.
    """

    config_entry: WiseMirrorConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WiseMirrorConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_HEARTBEAT_INTERVAL, DEFAULT_HEARTBEAT_INTERVAL
                )
            ),
        )
        self.mac: str | None = entry.data.get(CONF_MAC) or None
        # Mirrors added before their MAC could be read are keyed by IP.
        self.identifier: str = self.mac or entry.unique_id or entry.entry_id
        self.model: str | None = entry.data.get(CONF_MODEL)
        self.firmware: str | None = entry.data.get(CONF_FIRMWARE)
        self.ble_address: str | None = entry.data.get(CONF_BLE_ADDRESS)
        self.udp = WiseMirrorUdpClient(entry.data.get(CONF_HOST) or "")
        self.ble = WiseMirrorBleClient(self._ble_device, entry.title)
        self.device_name: str | None = None
        self._settings_interval = entry.options.get(
            CONF_SETTINGS_INTERVAL, DEFAULT_SETTINGS_INTERVAL
        )
        self._write_lock = asyncio.Lock()
        self._misses = 0
        self._settings_read_at: float | None = None
        self._settings_dirty = True
        self._heartbeat: Heartbeat | None = None
        self._probe: DiscoveryReply | None = None
        self._climate_source: str | None = None
        self._prefer_probe = False
        self._settings: Settings | None = None
        self._weather: WeatherSettings | None = None
        self._weather_supported = True
        self._advertisement: Advertisement | None = None
        self._advertisement_at: float | None = None
        self._transport: str | None = None

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------

    @property
    def host(self) -> str:
        """Current IP address, or an empty string when unknown."""
        return self.udp.host

    @property
    def version_number(self) -> int | None:
        """Firmware version byte as decimal."""
        if self._heartbeat is not None:
            return self._heartbeat.version_number
        return version_from_string(self.firmware)

    @property
    def weather_source_options(self) -> list[str]:
        """Weather-source options for this model and firmware."""
        return weather_source_options(self.model, self.version_number)

    @property
    def has_bluetooth(self) -> bool:
        """Only the 2M09 has Bluetooth; it is matched on its Wi-Fi MAC."""
        return self.mac is not None and self.model in (None, "2M09")

    # ------------------------------------------------------------------
    # Bluetooth
    # ------------------------------------------------------------------

    def _ble_device(self) -> BLEDevice | None:
        if not self.ble_address or "bluetooth" not in self.hass.config.components:
            return None
        return bluetooth.async_ble_device_from_address(
            self.hass, self.ble_address, connectable=True
        )

    def _ble_usable(self) -> bool:
        if self._advertisement_at is None:
            return False
        if time.monotonic() - self._advertisement_at > BLE_STALE_SECONDS:
            return False
        return self._ble_device() is not None

    @callback
    def async_handle_bluetooth(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Take the advertisement of this mirror (matched on its Wi-Fi MAC)."""
        adv = find_advertisement(service_info.manufacturer_data)
        if adv is None or adv.mac != self.mac:
            return
        self._advertisement = adv
        self._advertisement_at = time.monotonic()
        if service_info.address != self.ble_address:
            self.ble_address = service_info.address
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_BLE_ADDRESS: service_info.address},
            )
        if self.data is not None and self.data.advertisement != adv:
            self.data = self._build_data()
            self.async_update_listeners()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _build_data(self) -> MirrorData:
        data = MirrorData(
            heartbeat=self._heartbeat,
            probe=self._probe,
            settings=self._settings,
            weather=self._weather,
            advertisement=self._advertisement,
            transport=self._transport,
            host=self.host or None,
            firmware=self.firmware,
        )
        sources = {
            "heartbeat": self._heartbeat,
            "probe": self._probe,
            "advertisement": self._advertisement,
        }
        order = ["heartbeat", "probe", "advertisement"]
        if self._transport == TRANSPORT_BLUETOOTH:
            order = ["advertisement", "heartbeat", "probe"]
        elif self._climate_source in order:
            order.remove(self._climate_source)
            order.insert(0, self._climate_source)
        for name in order:
            if (source := sources[name]) is not None:
                return replace(
                    data,
                    temperature=source.temperature,
                    celsius=source.celsius,
                    humidity=source.humidity,
                )
        return data

    async def _async_heartbeat(self) -> bool:
        try:
            heartbeat = await self.udp.heartbeat()
        except (WiseMirrorError, ProtocolError) as err:
            _LOGGER.debug("Heartbeat to %s failed: %s", self.host, err)
            return False
        self._heartbeat = heartbeat
        self._climate_source = "heartbeat"
        self.firmware = heartbeat.firmware
        if heartbeat.device_name:
            self.device_name = heartbeat.device_name
        return True

    async def _async_probe(self) -> bool:
        """Unicast discovery query; tested hardware answers it reliably."""
        replies = await async_discover(self.host, timeout=PROBE_TIMEOUT)
        reply = next(
            (r for r in replies if self.mac is None or r.mac == self.mac), None
        )
        if reply is None:
            _LOGGER.debug("Discovery query to %s got no reply", self.host)
            return False
        self._probe = reply
        self._climate_source = "probe"
        if reply.version:
            self.firmware = reply.version
        if self.model is None:
            self.model = reply.model
        return True

    async def _async_poll_wifi(self) -> bool:
        if not self.host:
            return False
        methods = [self._async_heartbeat, self._async_probe]
        if self._prefer_probe:
            methods.reverse()
        for method in methods:
            if await method():
                self._prefer_probe = method == self._async_probe
                return True
        # Neither answered; a settings read still proves the mirror is there.
        try:
            self._settings = await self.udp.read_settings()
        except (WiseMirrorError, ProtocolError):
            return False
        self._settings_read_at = time.monotonic()
        self._settings_dirty = False
        return True

    async def _async_rediscover(self) -> bool:
        """Look for the mirror by MAC; adopt a changed IP address."""
        if self.mac is None:
            return False
        reply = await async_find_mirror(self.mac, self.host or None)
        if reply is None:
            return False
        return self.async_update_host(reply.host, reply.version)

    @callback
    def async_update_host(self, host: str, firmware: str | None = None) -> bool:
        """Adopt a new address (rediscovery, DHCP, or the broadcast scan)."""
        updates = {}
        if host != self.host:
            _LOGGER.info("%s moved from %s to %s", self.identifier, self.host, host)
            self.udp.host = host
            updates[CONF_HOST] = host
        if firmware and firmware != self.config_entry.data.get(CONF_FIRMWARE):
            updates[CONF_FIRMWARE] = firmware
        if updates:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, **updates}
            )
        return CONF_HOST in updates

    async def _async_read_settings(self, transport: str) -> None:
        if transport == TRANSPORT_BLUETOOTH:
            self._settings, self._weather = await self.ble.read_status()
        else:
            self._settings = await self.udp.read_settings()
            if self._weather_supported:
                try:
                    self._weather = await self.udp.read_weather()
                except (WiseMirrorError, ProtocolError) as err:
                    _LOGGER.debug("Weather read failed: %s", err)
                    if self._weather is None:
                        self._weather_supported = False
        self._settings_read_at = time.monotonic()
        self._settings_dirty = False

    def _settings_due(self) -> bool:
        return (
            self._settings_dirty
            or self._settings_read_at is None
            or time.monotonic() - self._settings_read_at >= self._settings_interval
        )

    async def _async_update_data(self) -> MirrorData:
        online = await self._async_poll_wifi()
        if online:
            self._misses = 0
        else:
            self._misses += 1
            # Rediscover on the first miss and every few misses after that,
            # since the DHCP address may have changed.
            if (self._misses == 1 or self._misses % MAX_MISSED_HEARTBEATS == 0) and (
                await self._async_rediscover()
            ):
                online = await self._async_poll_wifi()
                if online:
                    self._misses = 0

        previous = self._transport
        if online:
            self._transport = TRANSPORT_WIFI
        elif self._ble_usable():
            self._transport = TRANSPORT_BLUETOOTH
        elif self._misses >= MAX_MISSED_HEARTBEATS or self.data is None:
            self._transport = None
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={
                    "name": self.config_entry.title,
                    "host": self.host or "-",
                },
            )
        if self._transport != previous:
            # Re-read settings whenever the path to the mirror changes.
            self._settings_dirty = True

        if self._transport is not None and self._settings_due():
            try:
                await self._async_read_settings(self._transport)
            except (WiseMirrorError, ProtocolError) as err:
                _LOGGER.debug("Settings read failed: %s", err)
                if self._settings is None and self.data is None:
                    raise UpdateFailed(
                        translation_domain=DOMAIN,
                        translation_key="settings_failed",
                        translation_placeholders={
                            "name": self.config_entry.title,
                            "error": str(err),
                        },
                    ) from err

        return self._build_data()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def async_send(
        self,
        frame: bytes,
        *,
        settings: Settings | None = None,
        weather: WeatherSettings | None = None,
        refresh: bool = True,
    ) -> bytes | None:
        """Send a ready-made frame; see :meth:`async_write`."""
        return await self.async_write(
            lambda _data: (frame, settings, weather), refresh=refresh
        )

    async def async_write(
        self, build: WriteBuilder, *, refresh: bool = True
    ) -> bytes | None:
        """Send a command over Wi-Fi, falling back to Bluetooth.

        ``build`` gets the current state under the write lock, so commands
        that carry several fields (0x0D, 0x30) are built from the latest
        values. The expected settings/weather it returns are shown until the
        settings read that follows the write replaces them. Returns the UDP
        reply frame, or None when sent over Bluetooth.
        """
        async with self._write_lock:
            frame, settings, weather = build(self.data or self._build_data())
            reply, transport = await self._async_transmit(frame)

            command = frame[1]
            if command == Command.NAME and reply is not None:
                try:
                    if name_reply := parse_name_reply(reply):
                        self.firmware = name_reply.firmware
                except ProtocolError:
                    pass

            if settings is not None:
                self._settings = settings
            if weather is not None:
                self._weather = weather
            self._settings_dirty = True
            if refresh:
                try:
                    await self._async_read_settings(transport)
                except (WiseMirrorError, ProtocolError) as err:
                    _LOGGER.debug("Settings read after write failed: %s", err)
            if self.data is not None:
                self.data = self._build_data()
                self.async_update_listeners()
            return reply

    async def _async_transmit(self, frame: bytes) -> tuple[bytes | None, str]:
        command = frame[1]
        ble_possible = command not in UDP_ONLY_COMMANDS and self._ble_usable()
        transport = (
            TRANSPORT_BLUETOOTH
            if ble_possible and self._transport == TRANSPORT_BLUETOOTH
            else TRANSPORT_WIFI
        )
        reply: bytes | None = None
        try:
            if transport == TRANSPORT_WIFI:
                try:
                    reply = await self.udp.request(frame)
                except WiseMirrorRejected:
                    raise
                except WiseMirrorError:
                    if not ble_possible:
                        raise
                    _LOGGER.debug("UDP failed, sending 0x%02X over Bluetooth", command)
                    transport = TRANSPORT_BLUETOOTH
            if transport == TRANSPORT_BLUETOOTH:
                await self.ble.send_frame(frame)
        except WiseMirrorRejected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={
                    "command": f"0x{command:02X}",
                    "name": self.config_entry.title,
                },
            ) from err
        except WiseMirrorError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "command": f"0x{command:02X}",
                    "name": self.config_entry.title,
                    "error": str(err),
                },
            ) from err
        return reply, transport

    async def async_set_name(self, name: str) -> None:
        """Send 0x0F and remember the name until the heartbeat confirms it."""
        await self.async_send(build_name(name), refresh=False)
        self.device_name = name
        self.async_update_listeners()

    async def async_set_location(
        self, city: str, latitude: float, longitude: float, location_key: str = ""
    ) -> str:
        """Send 0x0B; return the location string that was sent."""
        location = format_location(city, latitude, longitude, location_key)

        def _build(
            data: MirrorData,
        ) -> tuple[bytes, Settings | None, WeatherSettings | None]:
            settings = (
                replace(data.settings, location=location) if data.settings else None
            )
            return build_location(location), settings, None

        await self.async_write(_build)
        return location

    async def async_sync_home_location(self) -> None:
        """Send Home Assistant's home location unless the mirror already has it."""
        config = self.hass.config
        latitude, longitude = config.latitude, config.longitude
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return
        if self.data is not None and self.data.settings is not None:
            current = parse_location(self.data.settings.location)
            if (
                current is not None
                and round(current.latitude, 2) == round(latitude, 2)
                and round(current.longitude, 2) == round(longitude, 2)
            ):
                return
        try:
            await self.async_set_location(
                config.location_name or "Home", latitude, longitude
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Could not send Home Assistant's location to %s: %s",
                self.config_entry.title,
                err,
            )
