"""End-to-end tests: entities against a simulated mirror on localhost."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wisemirror import coordinator as coordinator_module, transport
from custom_components.wisemirror.const import (
    CONF_BLE_ADDRESS,
    CONF_FIRMWARE,
    CONF_FOLLOW_HOME_LOCATION,
    CONF_MAC,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.wisemirror.diagnostics import async_get_config_entry_diagnostics
from custom_components.wisemirror.protocol import DiscoveryReply, parse_ble_status
from custom_components.wisemirror.transport import WiseMirrorUdpClient

from .fake_mirror import MAC, FakeMirror, MirrorState, ble_device, start_fake_mirror

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "REPLY_TIMEOUT", 0.1)
    monkeypatch.setattr(transport, "RETRY_INTERVAL", 0.1)
    monkeypatch.setattr(
        WiseMirrorUdpClient.request,
        "__kwdefaults__",
        {"tries": 3, "timeout": 0.1, "interval": 0.1},
    )


@pytest.fixture
def mirror_kwargs() -> dict:
    """Arguments for the simulated mirror; override per test."""
    return {}


@pytest.fixture
async def mirror(mirror_kwargs: dict) -> AsyncGenerator[FakeMirror]:
    protocol, udp = await start_fake_mirror(**mirror_kwargs)
    yield protocol
    udp.close()


@pytest.fixture
def find_mirror() -> AsyncMock:
    with patch.object(
        coordinator_module, "async_find_mirror", AsyncMock(return_value=None)
    ) as mock:
        yield mock


@pytest.fixture
def entry_options() -> dict:
    """Options for the config entry; override per test."""
    return {}


@pytest.fixture
async def entry(
    hass: HomeAssistant,
    mirror: FakeMirror,
    find_mirror: AsyncMock,
    entry_options: dict,
) -> AsyncGenerator[MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        title="Bathroom",
        data={
            CONF_HOST: "127.0.0.1",
            CONF_MAC: MAC,
            CONF_MODEL: "8J12",
            CONF_FIRMWARE: "V4.6.230601",
            CONF_BLE_ADDRESS: None,
        },
        options=entry_options,
    )
    entry.add_to_hass(hass)

    async def _discover(host, **kwargs):
        return await transport.async_discover(
            host, **{**kwargs, "port": mirror.port, "source_port": 0}
        )

    with (
        patch.object(
            coordinator_module,
            "WiseMirrorUdpClient",
            lambda host: WiseMirrorUdpClient(host, mirror.port),
        ),
        patch.object(coordinator_module, "async_discover", _discover),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def test_setup_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor.bathroom_temperature") == "22"
    assert (
        hass.states.get("sensor.bathroom_temperature").attributes["unit_of_measurement"]
        == "°C"
    )
    assert state(hass, "sensor.bathroom_humidity") == "45"
    assert state(hass, "sensor.bathroom_firmware") == "V4.6.230601"
    assert state(hass, "sensor.bathroom_connection") == "wifi"
    assert state(hass, "binary_sensor.bathroom_connectivity") == "on"
    assert state(hass, "sensor.bathroom_weather_location") == "Berlin"
    assert state(hass, "switch.bathroom_24_hour_clock") == "on"
    assert state(hass, "switch.bathroom_day_before_month") == "off"
    assert state(hass, "switch.bathroom_sleep_mode") == "on"
    assert state(hass, "switch.bathroom_two_day_weather") == "on"
    assert state(hass, "select.bathroom_temperature_unit") == "celsius"
    assert state(hass, "select.bathroom_weekday_language") == "english"
    assert state(hass, "select.bathroom_display_brightness_preset") == "medium"
    assert state(hass, "select.bathroom_sleep_brightness_preset") == "low"
    assert state(hass, "number.bathroom_display_brightness") == "50"
    assert state(hass, "number.bathroom_sleep_brightness") == "10"
    assert state(hass, "select.bathroom_weather_source") == "auto"
    assert hass.states.get("select.bathroom_weather_source").attributes["options"] == [
        "auto",
        "no_1",
        "no_2",
        "no_3",
        "no_4",
    ]
    assert state(hass, "time.bathroom_sleep_start") == "23:00:00"
    assert state(hass, "time.bathroom_sleep_end") == "06:30:00"
    assert state(hass, "text.bathroom_mirror_name") == "Bathroom"
    location = hass.states.get("text.bathroom_weather_location_string")
    assert location.state == "Berlin(52.52,13.40)()"
    assert location.attributes["city"] == "Berlin"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MAC)})
    assert device.sw_version == "V4.6.230601"
    assert device.model == "8J12"

    registry = er.async_get(hass)
    assert registry.async_get("sensor.bathroom_alarm_1").disabled
    assert registry.async_get("binary_sensor.bathroom_always_bright").disabled
    assert registry.async_get("switch.bathroom_key_tone").disabled
    assert registry.async_get("sensor.bathroom_ip_address").disabled
    assert not registry.async_get("sensor.bathroom_humidity").disabled


async def test_writes(
    hass: HomeAssistant, entry: MockConfigEntry, mirror: FakeMirror
) -> None:
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.bathroom_24_hour_clock"},
        blocking=True,
    )
    assert not mirror.state.flags & 0x02
    assert state(hass, "switch.bathroom_24_hour_clock") == "off"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bathroom_display_brightness", "value": 5},
        blocking=True,
    )
    assert mirror.state.brightness == 5
    assert state(hass, "number.bathroom_display_brightness") == "5"
    assert state(hass, "select.bathroom_display_brightness_preset") == "low"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bathroom_sleep_brightness", "value": 3},
        blocking=True,
    )
    assert mirror.state.sleep[5] == 3

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bathroom_display_brightness_preset", "option": "high"},
        blocking=True,
    )
    assert mirror.state.brightness == 86
    assert state(hass, "select.bathroom_display_brightness_preset") == "high"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bathroom_sleep_brightness_preset", "option": "high"},
        blocking=True,
    )
    assert mirror.state.sleep[5] == 30

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bathroom_temperature_unit", "option": "fahrenheit"},
        blocking=True,
    )
    assert not mirror.state.flags & 0x08

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bathroom_weekday_language", "option": "chinese"},
        blocking=True,
    )
    assert mirror.state.flags & 0x20

    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": "time.bathroom_sleep_end", "time": "07:15:00"},
        blocking=True,
    )
    assert mirror.state.sleep[1:5] == [23, 0, 7, 15]
    assert state(hass, "time.bathroom_sleep_end") == "07:15:00"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.bathroom_sleep_mode"}, blocking=True
    )
    assert mirror.state.sleep[0] == 0

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.bathroom_two_day_weather"},
        blocking=True,
    )
    # Bit 1 (today) is kept as read.
    assert mirror.state.weather_flags == 0x02
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bathroom_weather_source", "option": "no_4"},
        blocking=True,
    )
    assert (mirror.state.weather_flags, mirror.state.weather_source) == (0x02, 0)

    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": "text.bathroom_mirror_name", "value": "Hall"},
        blocking=True,
    )
    assert mirror.state.name == "Hall"
    assert state(hass, "text.bathroom_mirror_name") == "Hall"

    hass.config.location_name = "Köln"
    hass.config.latitude = 50.9375
    hass.config.longitude = 6.9603
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.bathroom_use_home_location"},
        blocking=True,
    )
    assert mirror.state.location == "K%C3%B6ln(50.94,6.96)()"

    await hass.services.async_call(
        "text",
        "set_value",
        {
            "entity_id": "text.bathroom_weather_location_string",
            "value": "Paris(48.86,2.35)(623)",
        },
        blocking=True,
    )
    assert mirror.state.location == "Paris(48.86,2.35)(623)"
    assert state(hass, "sensor.bathroom_weather_location") == "Paris"


async def test_services(
    hass: HomeAssistant, entry: MockConfigEntry, mirror: FakeMirror
) -> None:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MAC)})
    response = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {"device_id": device.id, "command": 6, "payload": "1e"},
        blocking=True,
        return_response=True,
    )
    assert response["request"] == "a5 06 00 01 00 01 1e 26 5a"
    assert response["reply"].startswith("a5 06")
    assert mirror.state.brightness == 30

    response = await hass.services.async_call(
        DOMAIN,
        "set_location",
        {
            "entity_id": "sensor.bathroom_temperature",
            "name": "Madrid",
            "latitude": 40.4168,
            "longitude": -3.7038,
        },
        blocking=True,
        return_response=True,
    )
    assert mirror.state.location == "Madrid(40.42,-3.70)()"

    response = await hass.services.async_call(
        DOMAIN,
        "set_location",
        {
            "device_id": [device.id],
            "city": "Berlin",
            "latitude": 52.52,
            "longitude": 13.4,
            "location_key": "178087",
        },
        blocking=True,
        return_response=True,
    )
    assert response == {"location": "Berlin(52.52,13.40)(178087)"}
    assert mirror.state.location == "Berlin(52.52,13.40)(178087)"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_location",
            {"device_id": "nope", "city": "X"},
            blocking=True,
        )


async def test_unavailable_after_four_misses(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mirror: FakeMirror,
    find_mirror: AsyncMock,
) -> None:
    coordinator = entry.runtime_data
    mirror.drop = 1000
    for _ in range(3):
        await coordinator.async_refresh()
        assert state(hass, "sensor.bathroom_temperature") == "22"
    assert find_mirror.await_count == 1
    await coordinator.async_refresh()
    assert state(hass, "sensor.bathroom_temperature") == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor.bathroom_connectivity") == "off"
    assert find_mirror.await_count == 2

    mirror.drop = 0
    mirror.state.temp = 25
    await coordinator.async_refresh()
    assert state(hass, "sensor.bathroom_temperature") == "25"


async def test_rediscovery_adopts_new_ip(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mirror: FakeMirror,
    find_mirror: AsyncMock,
) -> None:
    coordinator = entry.runtime_data
    coordinator.udp.host = "127.0.0.2"
    find_mirror.return_value = DiscoveryReply(
        model="8J12", mac=MAC, host="127.0.0.1", version="V4.6.230601"
    )
    await coordinator.async_refresh()
    assert coordinator.host == "127.0.0.1"
    assert entry.data[CONF_HOST] == "127.0.0.1"
    assert state(hass, "sensor.bathroom_connection") == "wifi"


async def test_bluetooth_fallback(
    hass: HomeAssistant, entry: MockConfigEntry, mirror: FakeMirror
) -> None:
    from habluetooth import BluetoothServiceInfoBleak

    coordinator = entry.runtime_data
    info = BluetoothServiceInfoBleak(
        name="WiseMirror_BLE",
        address="AA:BB:CC:DD:EE:01",
        rssi=-60,
        manufacturer_data={
            0x05F0: bytes([9, 0x03, 0, 19, 50]) + bytes.fromhex("aabbccddeeff")
        },
        service_data={},
        service_uuids=[],
        source="local",
        device=ble_device("AA:BB:CC:DD:EE:01", "WiseMirror_BLE"),
        advertisement=None,
        connectable=True,
        time=0,
        tx_power=None,
    )
    coordinator.async_handle_bluetooth(info, None)
    assert entry.data[CONF_BLE_ADDRESS] == "AA:BB:CC:DD:EE:01"

    status = MirrorState(brightness=30)
    from custom_components.wisemirror.protocol import encode_frame

    block = encode_frame(0x10, status.settings_payload(ble=True))
    mirror.drop = 1000
    with (
        patch.object(coordinator, "_ble_device", return_value=info.device),
        patch.object(
            coordinator.ble,
            "read_status",
            AsyncMock(return_value=parse_ble_status(block)),
        ),
        patch.object(coordinator.ble, "send_frame", AsyncMock()) as send_frame,
    ):
        await coordinator.async_refresh()
        assert state(hass, "sensor.bathroom_connection") == "bluetooth"
        assert state(hass, "sensor.bathroom_temperature") == "19"
        assert state(hass, "sensor.bathroom_humidity") == "50"
        assert state(hass, "select.bathroom_display_brightness_preset") == "low"
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.bathroom_24_hour_clock"},
            blocking=True,
        )
        assert send_frame.await_args.args[0][1] == 0x02


async def test_setup_not_ready(hass: HomeAssistant, find_mirror: AsyncMock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        title="Gone",
        data={CONF_HOST: "127.0.0.1", CONF_MAC: MAC, CONF_MODEL: "8J12"},
    )
    entry.add_to_hass(hass)
    protocol, udp = await start_fake_mirror(drop=1000)
    try:
        with patch.object(
            coordinator_module,
            "WiseMirrorUdpClient",
            lambda host: WiseMirrorUdpClient(host, protocol.port),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
    finally:
        udp.close()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_and_unload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["model"] == "8J12"
    assert diag["state"]["settings"]["location"] == "**REDACTED**"
    assert diag["state"]["weather"]["unknown"] == "00 00"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["entry"]["data"]["mac"] == "**REDACTED**"
    assert diag["entry"]["unique_id"] == "**REDACTED**"
    assert diag["state"]["host"] == "**REDACTED**"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("mirror_kwargs", [{"heartbeat": False}])
async def test_probe_fallback_without_heartbeat(
    hass: HomeAssistant, entry: MockConfigEntry, mirror: FakeMirror
) -> None:
    """A mirror that ignores 0x12 is polled with the discovery query."""
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor.bathroom_temperature") == "22"
    assert state(hass, "sensor.bathroom_humidity") == "45"
    assert state(hass, "sensor.bathroom_firmware") == "V4.6.230601"
    mirror.state.temp = 24
    mirror.received.clear()
    await entry.runtime_data.async_refresh()
    assert state(hass, "sensor.bathroom_temperature") == "24"
    # The working method is tried first from then on.
    assert mirror.received[0].startswith(b"What is the temperature")


@pytest.mark.parametrize(
    "mirror_kwargs", [{"state": MirrorState(humidity=0), "heartbeat": False}]
)
async def test_no_humidity_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Models without a humidity sensor report 0; the entity starts disabled."""
    registry = er.async_get(hass)
    assert registry.async_get("sensor.bathroom_humidity").disabled


@pytest.mark.parametrize("mirror_kwargs", [{"probe_host": "0.0.0.0"}])
async def test_probe_reply_without_address(
    hass: HomeAssistant, entry: MockConfigEntry, mirror: FakeMirror
) -> None:
    """A reply reporting 0.0.0.0 is taken from the address it came from."""
    mirror.heartbeat = False
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.data.probe.host == "127.0.0.1"


@pytest.mark.parametrize("entry_options", [{CONF_FOLLOW_HOME_LOCATION: True}])
async def test_follow_home_location(
    hass: HomeAssistant, mirror: FakeMirror, entry_options: dict
) -> None:
    hass.config.location_name = "Madrid"
    hass.config.latitude = 40.4168
    hass.config.longitude = -3.7038
    # Setting up the entry sends the home location.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        title="Bathroom",
        data={CONF_HOST: "127.0.0.1", CONF_MAC: MAC, CONF_MODEL: "8J12"},
        options=entry_options,
    )
    entry.add_to_hass(hass)
    with (
        patch.object(
            coordinator_module,
            "WiseMirrorUdpClient",
            lambda host: WiseMirrorUdpClient(host, mirror.port),
        ),
        patch.object(coordinator_module, "async_find_mirror", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert mirror.state.location == "Madrid(40.42,-3.70)()"

        # Unchanged home location: nothing is sent.
        mirror.received.clear()
        await hass.config.async_update(location_name="Madrid")
        await hass.async_block_till_done()
        assert not any(frame[1] == 0x0B for frame in mirror.received)

        await hass.config.async_update(latitude=41.3874, longitude=2.1686)
        await hass.async_block_till_done()
        assert mirror.state.location == "Madrid(41.39,2.17)()"


async def test_home_location_not_followed_by_default(
    hass: HomeAssistant, entry: MockConfigEntry, mirror: FakeMirror
) -> None:
    await hass.config.async_update(latitude=41.3874, longitude=2.1686)
    await hass.async_block_till_done()
    assert mirror.state.location == "Berlin(52.52,13.40)()"


async def test_background_scan(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from custom_components.wisemirror.discovery import async_handle_replies

    new = DiscoveryReply(model="2M09", mac="11:22:33:44:55:66", host="10.0.0.9")
    moved = DiscoveryReply(model="8J12", mac=MAC, host="127.0.0.1")
    async_handle_replies(hass, [new, moved])
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["unique_id"] for flow in flows] == ["11:22:33:44:55:66"]
    assert flows[0]["step_id"] == "discovery_confirm"


async def test_ip_keyed_entry(hass: HomeAssistant, mirror: FakeMirror) -> None:
    """An entry without a MAC (added through port 8001 only) still works."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="127.0.0.1",
        title="WiseMirror (127.0.0.1)",
        data={CONF_HOST: "127.0.0.1", CONF_MAC: None, CONF_MODEL: None},
    )
    entry.add_to_hass(hass)
    with patch.object(
        coordinator_module,
        "WiseMirrorUdpClient",
        lambda host: WiseMirrorUdpClient(host, mirror.port),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "127.0.0.1")})
    assert device is not None
    assert state(hass, "sensor.wisemirror_127_0_0_1_temperature") == "22"
