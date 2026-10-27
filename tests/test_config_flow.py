"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from habluetooth import BluetoothServiceInfoBleak
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:  # Home Assistant before 2025.2
    from homeassistant.components.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wisemirror.const import (
    CONF_APPLY_TO_ALL,
    CONF_BLE_ADDRESS,
    CONF_FOLLOW_HOME_LOCATION,
    CONF_HEARTBEAT_INTERVAL,
    CONF_MAC,
    CONF_MODEL,
    CONF_SETTINGS_INTERVAL,
    DOMAIN,
)
from custom_components.wisemirror.discovery import discovery_data
from custom_components.wisemirror.protocol import QUERY_TEMPERATURE, DiscoveryReply
from custom_components.wisemirror.transport import WiseMirrorTimeout

from .fake_mirror import ble_device

MAC = "aa:bb:cc:dd:ee:ff"
REPLY = DiscoveryReply(
    model="8J12", mac=MAC, host="192.168.1.50", word="LEDWifi", version="V4.6.230601"
)
BLE_ADDRESS = "AA:BB:CC:DD:EE:01"


def _service_info(
    flags: int, name: str = "WiseMirror_BLE"
) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name,
        address=BLE_ADDRESS,
        rssi=-60,
        manufacturer_data={
            0x05F0: bytes([9, flags, 0, 22, 40]) + bytes.fromhex("aabbccddeeff")
        },
        service_data={},
        service_uuids=[],
        source="local",
        device=ble_device(BLE_ADDRESS, name),
        advertisement=None,
        connectable=True,
        time=0,
        tx_power=None,
    )


def _patch_setup():
    return patch("custom_components.wisemirror.async_setup_entry", return_value=True)


def _patch_name(name: str | None = "Bathroom"):
    return patch(
        "custom_components.wisemirror.config_flow._async_mirror_name",
        AsyncMock(return_value=name or REPLY.default_name),
    )


async def test_user_discovered(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.wisemirror.config_flow.async_discover",
        AsyncMock(return_value=[REPLY]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with _patch_setup(), _patch_name():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": MAC}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bathroom"
    assert result["data"][CONF_HOST] == "192.168.1.50"
    assert result["data"][CONF_MAC] == MAC
    assert result["data"][CONF_MODEL] == "8J12"
    assert result["result"].unique_id == MAC


async def test_user_manual(hass: HomeAssistant) -> None:
    query = AsyncMock(side_effect=[None, REPLY])
    with (
        patch(
            "custom_components.wisemirror.config_flow.async_discover",
            AsyncMock(return_value=[]),
        ),
        patch("custom_components.wisemirror.config_flow._async_query", query),
        patch(
            "custom_components.wisemirror.config_flow._async_settings_answer",
            AsyncMock(return_value=False),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.50"}
        )
        assert result["errors"] == {"base": "cannot_connect"}
        with _patch_setup(), _patch_name(None):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_HOST: "192.168.1.50"}
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WiseMirror-EEFF"


async def test_user_manual_control_port_only(hass: HomeAssistant) -> None:
    """A mirror that only answers on port 8001 is keyed by its address."""
    with (
        patch(
            "custom_components.wisemirror.config_flow.async_discover",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.wisemirror.config_flow._async_query",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.wisemirror.config_flow._async_settings_answer",
            AsyncMock(return_value=True),
        ),
        _patch_setup(),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.60"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WiseMirror (192.168.1.60)"
    assert result["result"].unique_id == "192.168.1.60"
    assert result["data"][CONF_MAC] is None


async def test_reconfigure(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "1.2.3.4", CONF_MAC: MAC}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with (
        patch(
            "custom_components.wisemirror.config_flow._async_query",
            AsyncMock(return_value=REPLY),
        ),
        _patch_setup(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.50"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.50"


async def test_reconfigure_wrong_device(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={CONF_HOST: "1.2.3.4", CONF_MAC: "11:22:33:44:55:66"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.wisemirror.config_flow._async_query",
        AsyncMock(return_value=REPLY),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.50"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_HOST] == "1.2.3.4"


async def test_reconfigure_cannot_connect(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "1.2.3.4", CONF_MAC: MAC}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with (
        patch(
            "custom_components.wisemirror.config_flow._async_query",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.wisemirror.config_flow._async_settings_answer",
            AsyncMock(return_value=False),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.99"}
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_integration_discovery(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
        data=discovery_data(REPLY),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    with _patch_setup(), _patch_name():
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.168.1.50"
    assert result["data"][CONF_MODEL] == "8J12"


async def test_integration_discovery_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "1.2.3.4", CONF_MAC: MAC}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
        data=discovery_data(REPLY),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.50"


async def test_dhcp_mirror(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.wisemirror.config_flow._async_query",
        AsyncMock(return_value=REPLY),
    ) as query:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=DhcpServiceInfo(
                ip="192.168.1.50", hostname="esp_ddeeff", macaddress="aabbccddeeff"
            ),
        )
    query.assert_awaited_once_with("192.168.1.50", (QUERY_TEMPERATURE,))
    assert result["step_id"] == "discovery_confirm"


async def test_dhcp_not_a_mirror(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.wisemirror.config_flow._async_query",
        AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=DhcpServiceInfo(
                ip="192.168.1.77", hostname="espressif", macaddress="aabbcc000000"
            ),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_wisemirror"


async def test_user_already_configured_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "1.2.3.4", CONF_MAC: MAC}
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.wisemirror.config_flow.async_discover",
        AsyncMock(return_value=[REPLY]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Already configured mirrors are not offered.
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.50"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.50"


async def test_bluetooth_provisioned_found_on_lan(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_service_info(0x03),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    with (
        patch(
            "custom_components.wisemirror.config_flow.async_find_mirror",
            AsyncMock(
                return_value=DiscoveryReply(model="2M09", mac=MAC, host="10.0.0.9")
            ),
        ),
        _patch_setup(),
        _patch_name(None),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "10.0.0.9"
    assert result["data"][CONF_BLE_ADDRESS] == BLE_ADDRESS


async def test_bluetooth_provisioned_bluetooth_only(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_service_info(0x03),
    )
    with (
        patch(
            "custom_components.wisemirror.config_flow.async_find_mirror",
            AsyncMock(return_value=None),
        ),
        _patch_setup(),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == ""
    assert result["data"][CONF_MODEL] == "2M09"


async def test_bluetooth_not_wisemirror(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_service_info(0x03, name="Other"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_provisioning(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_service_info(0x02),
    )
    assert result["step_id"] == "wifi"
    send = AsyncMock()
    first_contact = AsyncMock(return_value=None)
    with (
        patch(
            "custom_components.wisemirror.config_flow.WiseMirrorBleClient.send_wifi_credentials",
            send,
        ),
        patch(
            "custom_components.wisemirror.config_flow.async_find_mirror",
            AsyncMock(
                return_value=DiscoveryReply(model="2M09", mac=MAC, host="10.0.0.9")
            ),
        ),
        patch(
            "custom_components.wisemirror.config_flow.async_first_contact",
            first_contact,
        ),
        _patch_setup(),
        _patch_name(None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"ssid": "HomeNet", CONF_PASSWORD: "secret"}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    send.assert_awaited_once_with("HomeNet", "secret")
    assert first_contact.await_args.args[0] == "10.0.0.9"
    assert first_contact.await_args.args[1] == "WiseMirror-EEFF"
    assert result["data"][CONF_HOST] == "10.0.0.9"


async def test_bluetooth_provisioning_ble_error(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_service_info(0x02),
    )
    with patch(
        "custom_components.wisemirror.config_flow.WiseMirrorBleClient.send_wifi_credentials",
        AsyncMock(side_effect=WiseMirrorTimeout("nope")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"ssid": "HomeNet", CONF_PASSWORD: "secret"}
        )
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "wifi"
    assert result["errors"] == {"base": "ble_failed"}


async def test_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "1.2.3.4", CONF_MAC: MAC}
    )
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={CONF_HOST: "1.2.3.5", CONF_MAC: "11:22:33:44:55:66"},
        options={CONF_FOLLOW_HOME_LOCATION: True},
    )
    entry.add_to_hass(hass)
    other.add_to_hass(hass)
    with _patch_setup():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_HEARTBEAT_INTERVAL: 10,
                CONF_SETTINGS_INTERVAL: 60,
                CONF_APPLY_TO_ALL: True,
                CONF_FOLLOW_HOME_LOCATION: True,
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_HEARTBEAT_INTERVAL: 10,
        CONF_SETTINGS_INTERVAL: 60,
        CONF_FOLLOW_HOME_LOCATION: True,
    }
    # Intervals copied, the other mirror's own options kept.
    assert other.options == {
        CONF_HEARTBEAT_INTERVAL: 10,
        CONF_SETTINGS_INTERVAL: 60,
        CONF_FOLLOW_HOME_LOCATION: True,
    }
