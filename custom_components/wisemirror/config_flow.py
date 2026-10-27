"""Config flow for WiseMirror."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Any

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
import voluptuous as vol

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .ble import WiseMirrorBleClient
from .const import (
    CONF_APPLY_TO_ALL,
    CONF_BLE_ADDRESS,
    CONF_FIRMWARE,
    CONF_FOLLOW_HOME_LOCATION,
    CONF_HEARTBEAT_INTERVAL,
    CONF_MAC,
    CONF_MODEL,
    CONF_SETTINGS_INTERVAL,
    DEFAULT_FOLLOW_HOME_LOCATION,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_SETTINGS_INTERVAL,
    DOMAIN,
    MAX_HEARTBEAT_INTERVAL,
    MAX_SETTINGS_INTERVAL,
    MIN_HEARTBEAT_INTERVAL,
    MIN_SETTINGS_INTERVAL,
    PROVISION_DISCOVERY_SECONDS,
)
from .helpers import async_first_contact, home_location
from .protocol import (
    MODEL_2M09,
    QUERY_NEW_DEVICE,
    QUERY_OLD_DEVICE,
    QUERY_TEMPERATURE,
    Advertisement,
    DiscoveryReply,
    ProtocolError,
    find_advertisement,
    is_wisemirror_ble_name,
)
from .transport import (
    WiseMirrorError,
    WiseMirrorUdpClient,
    async_discover,
    async_find_mirror,
)

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE = "device"
CONF_SSID = "ssid"
MANUAL = "manual"


ALL_QUERIES = (QUERY_TEMPERATURE, QUERY_OLD_DEVICE, QUERY_NEW_DEVICE)


async def _async_query(
    host: str, queries: tuple[str, ...] = ALL_QUERIES
) -> DiscoveryReply | None:
    """Ask one address with each discovery query until a mirror answers."""
    for query in queries:
        if replies := await async_discover(host, query=query):
            return replies[0]
    return None


async def _async_settings_answer(host: str) -> bool:
    """True when the address answers a settings read on the control port."""
    try:
        await WiseMirrorUdpClient(host).read_settings()
    except (WiseMirrorError, ProtocolError):
        return False
    return True


async def _async_mirror_name(reply: DiscoveryReply) -> str:
    """Name from the heartbeat JSON, else the default name."""
    try:
        heartbeat = await WiseMirrorUdpClient(reply.host).heartbeat()
    except (WiseMirrorError, ProtocolError):
        return reply.default_name
    return heartbeat.device_name or reply.default_name


class WiseMirrorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover mirrors over UDP or Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered: dict[str, DiscoveryReply] = {}
        self._advertisement: Advertisement | None = None
        self._ble_device: BLEDevice | None = None
        self._ble_address: str | None = None
        self._ssid: str | None = None
        self._password: str | None = None
        self._provision_task: asyncio.Task[DiscoveryReply | None] | None = None
        self._provision_error: str | None = None
        self._provisioned: DiscoveryReply | None = None
        self._discovered_reply: DiscoveryReply | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WiseMirrorOptionsFlow:
        """Return the options flow."""
        return WiseMirrorOptionsFlow()

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a discovered mirror or enter an address."""
        if user_input is not None:
            if user_input[CONF_DEVICE] == MANUAL:
                return await self.async_step_manual()
            return await self._async_create_from_reply(
                self._discovered[user_input[CONF_DEVICE]]
            )

        configured = self._async_current_ids(include_ignore=False)
        self._discovered = {
            reply.mac: reply
            for reply in await async_discover()
            if reply.mac not in configured
        }
        if not self._discovered:
            return await self.async_step_manual()
        options = [
            SelectOptionDict(
                value=mac, label=f"{reply.default_name} ({reply.model}, {reply.host})"
            )
            for mac, reply in self._discovered.items()
        ]
        options.append(SelectOptionDict(value=MANUAL, label=MANUAL))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): SelectSelector(
                        SelectSelectorConfig(
                            options=options, translation_key=CONF_DEVICE
                        )
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Query one address with a unicast discovery request."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if reply := await _async_query(host):
                return await self._async_create_from_reply(reply)
            if await _async_settings_answer(host):
                # Answers control commands but not discovery: key it by IP.
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"WiseMirror ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_MAC: None,
                        CONF_MODEL: None,
                        CONF_FIRMWARE: None,
                        CONF_BLE_ADDRESS: None,
                    },
                )
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the IP address of a configured mirror."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            reply = await _async_query(host)
            if reply is None and not await _async_settings_answer(host):
                errors["base"] = "cannot_connect"
            else:
                if reply is not None and entry.data.get(CONF_MAC):
                    await self.async_set_unique_id(reply.mac)
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                data = {**entry.data, CONF_HOST: host}
                if reply is not None and reply.version:
                    data[CONF_FIRMWARE] = reply.version
                self.hass.config_entries.async_update_entry(entry, data=data)
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}),
                user_input or {CONF_HOST: entry.data.get(CONF_HOST, "")},
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Automatic discovery: background broadcast and DHCP
    # ------------------------------------------------------------------

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """A mirror answered the background broadcast."""
        reply = DiscoveryReply(
            model=discovery_info[CONF_MODEL],
            mac=discovery_info[CONF_MAC],
            host=discovery_info[CONF_HOST],
            version=discovery_info.get(CONF_FIRMWARE),
        )
        return await self._async_discovered(reply)

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """A device that may be a mirror joined the network; ask it."""
        # Every Espressif device can land here: send only the query that
        # mirrors are known to answer, so non-mirrors cost one timeout.
        reply = await _async_query(discovery_info.ip, (QUERY_TEMPERATURE,))
        if reply is None:
            return self.async_abort(reason="not_wisemirror")
        return await self._async_discovered(reply)

    async def _async_discovered(self, reply: DiscoveryReply) -> ConfigFlowResult:
        await self.async_set_unique_id(reply.mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: reply.host})
        self._async_abort_entries_match({CONF_HOST: reply.host})
        self._discovered_reply = reply
        self.context["title_placeholders"] = {"name": reply.default_name}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered mirror."""
        assert self._discovered_reply is not None
        reply = self._discovered_reply
        if user_input is None:
            self._set_confirm_only()
            return self.async_show_form(
                step_id="discovery_confirm",
                description_placeholders={
                    "name": reply.default_name,
                    "model": reply.model,
                    "host": reply.host,
                },
            )
        return await self._async_create_from_reply(reply)

    async def _async_create_from_reply(
        self, reply: DiscoveryReply, ble_address: str | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(reply.mac)
        updates: dict[str, Any] = {CONF_HOST: reply.host}
        if ble_address:
            updates[CONF_BLE_ADDRESS] = ble_address
        self._abort_if_unique_id_configured(updates=updates)
        return self.async_create_entry(
            title=await _async_mirror_name(reply),
            data={
                CONF_HOST: reply.host,
                CONF_MAC: reply.mac,
                CONF_MODEL: reply.model,
                CONF_FIRMWARE: reply.version,
                CONF_BLE_ADDRESS: ble_address,
            },
        )

    # ------------------------------------------------------------------
    # Bluetooth (2M09)
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a 2M09 advertisement."""
        if not is_wisemirror_ble_name(discovery_info.name):
            return self.async_abort(reason="not_supported")
        adv = find_advertisement(discovery_info.manufacturer_data)
        if adv is None:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(adv.mac)
        self._abort_if_unique_id_configured(
            updates={CONF_BLE_ADDRESS: discovery_info.address}
        )
        self._advertisement = adv
        self._ble_device = discovery_info.device
        self._ble_address = discovery_info.address
        self.context["title_placeholders"] = {"name": adv.default_name}
        if adv.provisioned:
            return await self.async_step_bluetooth_confirm()
        return await self.async_step_wifi()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a mirror that is already on Wi-Fi."""
        assert self._advertisement is not None
        adv = self._advertisement
        if user_input is None:
            self._set_confirm_only()
            return self.async_show_form(
                step_id="bluetooth_confirm",
                description_placeholders={"name": adv.default_name},
            )
        if reply := await async_find_mirror(adv.mac):
            return await self._async_create_from_reply(reply, self._ble_address)
        # Not found on the LAN: control it over Bluetooth until it shows up.
        return self.async_create_entry(
            title=adv.default_name,
            data={
                CONF_HOST: "",
                CONF_MAC: adv.mac,
                CONF_MODEL: adv.model or MODEL_2M09,
                CONF_FIRMWARE: None,
                CONF_BLE_ADDRESS: self._ble_address,
            },
        )

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Wi-Fi credentials to send over Bluetooth."""
        assert self._advertisement is not None
        errors: dict[str, str] = {}
        if self._provision_error:
            errors["base"] = self._provision_error
            self._provision_error = None
        elif user_input is not None:
            self._ssid = user_input[CONF_SSID]
            self._password = user_input.get(CONF_PASSWORD, "")
            return await self.async_step_provision()
        return self.async_show_form(
            step_id="wifi",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SSID, default=self._ssid or ""): str,
                    vol.Optional(CONF_PASSWORD, default=self._password or ""): str,
                }
            ),
            description_placeholders={"name": self._advertisement.default_name},
            errors=errors,
        )

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the credentials and wait for the mirror to join the LAN."""
        if self._provision_task is None:
            self._provision_task = self.hass.async_create_task(
                self._async_provision(), eager_start=False
            )
        if not self._provision_task.done():
            return self.async_show_progress(
                step_id="provision",
                progress_action="provision",
                progress_task=self._provision_task,
            )
        task, self._provision_task = self._provision_task, None
        try:
            self._provisioned = task.result()
        except WiseMirrorError as err:
            _LOGGER.debug("Bluetooth provisioning failed: %s", err)
            self._provision_error = "ble_failed"
            return self.async_show_progress_done(next_step_id="wifi")
        if self._provisioned is None:
            self._provision_error = "not_found"
            return self.async_show_progress_done(next_step_id="wifi")
        return self.async_show_progress_done(next_step_id="provision_finish")

    async def _async_provision(self) -> DiscoveryReply | None:
        assert self._advertisement is not None
        adv = self._advertisement
        address = self._ble_address

        def _device() -> BLEDevice | None:
            return (
                bluetooth.async_ble_device_from_address(
                    self.hass, address, connectable=True
                )
                if address
                else None
            ) or self._ble_device

        await WiseMirrorBleClient(_device, adv.default_name).send_wifi_credentials(
            self._ssid or "", self._password or ""
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PROVISION_DISCOVERY_SECONDS
        while loop.time() < deadline:
            if reply := await async_find_mirror(adv.mac):
                try:
                    if name_reply := await async_first_contact(
                        reply.host, adv.default_name, home_location(self.hass)
                    ):
                        reply = replace(
                            reply, version=reply.version or name_reply.firmware
                        )
                except WiseMirrorError as err:
                    _LOGGER.debug("First contact failed: %s", err)
                return reply
            await asyncio.sleep(3)
        return None

    async def async_step_provision_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry for the freshly provisioned mirror."""
        assert self._provisioned is not None
        return await self._async_create_from_reply(self._provisioned, self._ble_address)


class WiseMirrorOptionsFlow(OptionsFlow):
    """Polling intervals and following the home location."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the options."""
        if user_input is not None:
            apply_to_all = user_input.pop(CONF_APPLY_TO_ALL, False)
            user_input[CONF_HEARTBEAT_INTERVAL] = int(
                user_input[CONF_HEARTBEAT_INTERVAL]
            )
            user_input[CONF_SETTINGS_INTERVAL] = int(user_input[CONF_SETTINGS_INTERVAL])
            if apply_to_all:
                intervals = {
                    CONF_HEARTBEAT_INTERVAL: user_input[CONF_HEARTBEAT_INTERVAL],
                    CONF_SETTINGS_INTERVAL: user_input[CONF_SETTINGS_INTERVAL],
                }
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if entry.entry_id != self.config_entry.entry_id:
                        self.hass.config_entries.async_update_entry(
                            entry, options={**entry.options, **intervals}
                        )
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options

        def _seconds(minimum: int, maximum: int) -> NumberSelector:
            return NumberSelector(
                NumberSelectorConfig(
                    min=minimum,
                    max=maximum,
                    step=5,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HEARTBEAT_INTERVAL,
                        default=options.get(
                            CONF_HEARTBEAT_INTERVAL, DEFAULT_HEARTBEAT_INTERVAL
                        ),
                    ): _seconds(MIN_HEARTBEAT_INTERVAL, MAX_HEARTBEAT_INTERVAL),
                    vol.Required(
                        CONF_SETTINGS_INTERVAL,
                        default=options.get(
                            CONF_SETTINGS_INTERVAL, DEFAULT_SETTINGS_INTERVAL
                        ),
                    ): _seconds(MIN_SETTINGS_INTERVAL, MAX_SETTINGS_INTERVAL),
                    vol.Required(CONF_APPLY_TO_ALL, default=False): bool,
                    vol.Required(
                        CONF_FOLLOW_HOME_LOCATION,
                        default=options.get(
                            CONF_FOLLOW_HOME_LOCATION, DEFAULT_FOLLOW_HOME_LOCATION
                        ),
                    ): bool,
                }
            ),
        )
