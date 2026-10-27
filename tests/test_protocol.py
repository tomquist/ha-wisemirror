"""Tests for the wire protocol, using the vectors from the protocol document."""

from __future__ import annotations

import pytest

from custom_components.wisemirror import protocol as p

from .fake_mirror import MirrorState, heartbeat_reply


def h(value: str) -> bytes:
    return bytes.fromhex(value)


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (p.build_brightness(50), "A5 06 00 01 00 01 32 3A 5A"),
        (p.build_heartbeat(), "A5 12 00 01 00 01 00 14 5A"),
        (p.build_read_settings(), "A5 10 00 01 00 01 00 12 5A"),
        (p.build_sleep_window(23, 0, 6, 30), "A5 0D 00 04 00 01 17 00 06 1E 4D 5A"),
        (p.build_weather(0x01, 255), "A5 30 00 04 00 01 01 FF 00 00 35 5A"),
        (p.build_name("Bad"), "A5 0F 00 04 00 01 03 42 61 64 1E 5A"),
        (
            p.build_location("Berlin(52.52,13.40)()"),
            "A5 0B 00 17 00 01 00 15 42 65 72 6C 69 6E 28 35 32 2E 35 32 2C 31 33 2E"
            " 34 30 29 28 29 54 5A",
        ),
        (p.build_firmware_update(5000), "A5 13 00 02 00 01 13 88 B1 5A"),
    ],
)
def test_frame_vectors(frame: bytes, expected: str) -> None:
    assert frame == h(expected)


def test_ble_chunk_vectors() -> None:
    assert p.chunk_message(p.build_name("Bathroom mirror"), 0x37) == [
        h("37 18 00 A5 0F 00 10 00 01 0F 42 61 74 68 72 6F 6F 6D 20 6D"),
        h("37 18 11 69 72 72 6F 72 26 5A"),
    ]
    creds = p.build_wifi_credentials_json("HomeNet", "secret123")
    assert creds == b'{"pwd":"secret123","ssid":"HomeNet"}'
    assert p.chunk_message(creds, 0x37) == [
        h("37 24 00 7B 22 70 77 64 22 3A 22 73 65 63 72 65 74 31 32 33"),
        h("37 24 11 22 2C 22 73 73 69 64 22 3A 22 48 6F 6D 65 4E 65 74"),
        h("37 24 22 22 7D"),
    ]


def test_chunk_rejects_bad_ids() -> None:
    for bad in (0, 0x5A, 255):
        with pytest.raises(ValueError):
            p.chunk_message(b"x", bad)
    with pytest.raises(ValueError):
        p.chunk_message(bytes(256), 1)


def test_location_format_and_parse() -> None:
    value = p.format_location("Frankfurt am Main", 50.1109, 8.6821, "168720")
    assert value == "Frankfurt%20am%20Main(50.11,8.68)(168720)"
    loc = p.parse_location(value)
    assert loc == p.Location("Frankfurt am Main", 50.11, 8.68, "168720")
    assert p.parse_location("garbage") is None
    assert p.format_location("Berlin", 52.52, 13.4) == "Berlin(52.52,13.40)()"


def test_decode_frame_checks() -> None:
    frame = p.decode_frame(p.build_brightness(50))
    assert frame.command == p.Command.BRIGHTNESS
    assert frame.payload == b"\x32"
    assert frame.accepted
    with pytest.raises(p.ProtocolError):
        p.decode_frame(h("A5 06 00 01 00 01 32 3B 5A"))
    with pytest.raises(p.ProtocolError):
        p.decode_frame(h("A5 06 00 01 00 01 32 3A 00"))
    with pytest.raises(p.ProtocolError):
        p.decode_frame(h("A5 06 00 01 00 01 32 3A"))
    assert p.is_success_reply(p.build_brightness(50), 0x06)
    assert not p.is_success_reply(p.encode_frame(0x06, b"\x00", status=0), 0x06)


def test_heartbeat() -> None:
    hb = p.parse_heartbeat(
        heartbeat_reply(temp=-3, info={"mainDeviceName": "Hall", "unit": "c"})
    )
    assert hb.temperature == -3
    assert hb.celsius
    assert hb.firmware == "V4.6.230601"
    assert hb.version_number == 46
    assert hb.humidity == 45
    assert hb.device_name == "Hall"
    # No JSON, no humidity sensor, invalid temperature.
    hb = p.parse_heartbeat(heartbeat_reply(temp=120, humidity=0))
    assert hb.temperature is None
    # 0 means the model has no humidity sensor.
    assert hb.humidity is None
    assert p.parse_heartbeat(heartbeat_reply(humidity=120)).humidity == 99
    assert hb.info == {}
    # The 2M09 version byte 0x19 is V1.9.
    assert (
        p.parse_heartbeat(heartbeat_reply(version=0x19, date=(25, 2, 15))).firmware
        == "V1.9.250215"
    )


def test_settings_block() -> None:
    state = MirrorState(flags=0b0011_1111, sleep=[1, 22, 15, 6, 45, 20])
    settings = p.parse_settings(p.encode_frame(0x10, state.settings_payload()))
    assert settings.key_tone and settings.clock_24h and settings.day_first
    assert settings.celsius and settings.always_bright and settings.chinese_weekday
    assert settings.brightness == 50
    assert settings.alarms[0] == p.Alarm(True, 7, 30, 0x1F)
    assert settings.alarms[0].weekday_list == [0, 1, 2, 3, 4]
    assert settings.sleep_on
    assert (settings.sleep_start_hour, settings.sleep_start_minute) == (22, 15)
    assert (settings.sleep_end_hour, settings.sleep_end_minute) == (6, 45)
    assert settings.sleep_brightness == 20
    assert settings.location == "Berlin(52.52,13.40)()"
    assert not settings.with_flag(p.SETTINGS_FLAG_KEY_TONE, False).key_tone
    with pytest.raises(p.ProtocolError):
        p.parse_settings(p.encode_frame(0x10, b"\x00" * 10))


def test_ble_status_block() -> None:
    state = MirrorState(weather_flags=0x01, weather_source=4)
    settings, weather = p.parse_ble_status(
        p.encode_frame(0x10, state.settings_payload(ble=True))
    )
    assert settings.location == state.location
    assert settings.sleep_brightness == 10
    assert weather.two_day and not weather.today
    assert weather.source == 4


def test_weather_block() -> None:
    weather = p.parse_weather(p.encode_frame(0x16, MirrorState().weather_payload()))
    assert weather.flags == 0x03 and weather.two_day and weather.today
    assert weather.source == 255
    assert weather.location == "Berlin(52.52,13.40)()"


def test_weather_source_options() -> None:
    assert p.weather_source_options("8J11", 25) == ["no_1", "no_2", "no_3"]
    assert p.weather_source_options("8J12", 46) == [
        "auto",
        "no_1",
        "no_2",
        "no_3",
        "no_4",
    ]
    assert p.weather_source_options("2K02", 22) == ["no_1", "no_2", "no_3"]
    assert p.weather_source_options("2K02", 23)[0] == "auto"
    assert p.weather_source_options("2M09", 10)[-1] == "no_5"
    assert p.weather_source_options("8J12", None) == ["no_1", "no_2", "no_3"]
    assert p.weather_source_key(0) == "no_4"
    assert p.weather_source_key(255) == "auto"
    assert p.weather_source_key(77) is None


def test_discovery_temperature_reply() -> None:
    reply = p.parse_discovery_reply(
        b"LEDWifi 8J12+21+C+V4.6.230601+aa:bb:cc:dd:ee:ff+192.168.1.50,48"
    )
    assert reply is not None
    assert reply.model == "8J12"
    assert reply.word == "LEDWifi"
    assert reply.temperature == 21
    assert reply.celsius is True
    assert reply.version == "V4.6.230601"
    assert reply.version_number == 46
    assert reply.mac == "aa:bb:cc:dd:ee:ff"
    assert reply.host == "192.168.1.50"
    assert reply.humidity == 48
    assert reply.default_name == "WiseMirror-EEFF"


def test_discovery_real_2m09_capture() -> None:
    """Reply captured from an Alasta 2M09 (jorgediez/ha-wisemirror)."""
    reply = p.parse_discovery_reply(
        "I'm 2M09+25+1+V1.9.250215+30:30:f9:f3:ce:b8+192.168.1.244,0"
    )
    assert reply is not None
    assert reply.model == "2M09"
    assert reply.temperature == 25
    assert reply.celsius is True
    assert reply.version_number == 19
    assert reply.mac == "30:30:f9:f3:ce:b8"
    assert reply.host == "192.168.1.244"
    assert reply.humidity is None
    assert reply.default_name == "WiseMirror-CEB8"


def test_discovery_zero_address_uses_source() -> None:
    text = "I'm 2M09+25+1+V1.9.250215+30:30:f9:f3:ce:b8+0.0.0.0,40"
    assert p.parse_discovery_reply(text) is None
    reply = p.parse_discovery_reply(text, "10.0.0.5")
    assert reply is not None and reply.host == "10.0.0.5"
    assert reply.humidity == 40


def test_discovery_device_replies() -> None:
    new = p.parse_discovery_reply("LEDWifi 2M09+0+AA:BB:CC:DD:EE:01+10.0.0.7")
    assert new is not None and new.flag == "0" and new.model == "2M09"
    assert new.mac == "aa:bb:cc:dd:ee:01"
    old = p.parse_discovery_reply("LEDWifi 8J11+1+aa:bb:cc:dd:ee:01+10.0.0.7")
    assert old is not None and old.flag == "1"


@pytest.mark.parametrize(
    "text",
    [
        "LEDWifi 9X99+0+aa:bb:cc:dd:ee:01+10.0.0.7",
        "LEDWifi 8J12+0+aa:bb:cc:dd:ee:01+0.0.0.0",
        "LEDWifi 8J12+0",
        "LEDWifi 8J12+0+nonsense+10.0.0.7",
        "",
    ],
)
def test_discovery_ignored(text: str) -> None:
    assert p.parse_discovery_reply(text) is None


def test_advertisement() -> None:
    data = h("09 03 00 17 2D AA BB CC DD EE FF")
    adv = p.parse_advertisement(data)
    assert adv == p.Advertisement(
        model="2M09",
        model_code=9,
        provisioned=True,
        celsius=True,
        temperature=23,
        humidity=45,
        mac="aa:bb:cc:dd:ee:ff",
    )
    assert adv.default_name == "WiseMirror-EEFF"
    neg = p.parse_advertisement(h("09 00 FF F6 2D AA BB CC DD EE FF"))
    assert neg.temperature == -10 and not neg.celsius and not neg.provisioned
    assert (
        p.parse_advertisement(h("09 02 03 E8 2D AA BB CC DD EE FF")).temperature is None
    )
    assert p.parse_advertisement(b"\x09\x00") is None
    assert p.find_advertisement({0x1234: b"\x01", 0x5678: data}) == adv
    assert p.is_wisemirror_ble_name("WiseMirror_BLE_1234")
    assert p.is_wisemirror_ble_name("WISE")
    assert not p.is_wisemirror_ble_name("WISEMIRROR")
    assert not p.is_wisemirror_ble_name(None)


def test_ble_ack() -> None:
    ack = p.parse_ble_ack(h("37 18 00 A5 0F 00 01"))
    assert ack is not None and ack.msg_id == 0x37 and ack.offset == 0
    assert ack.command == 0x0F and ack.is_frame_ack
    creds_ack = p.parse_ble_ack(h("37 24 00 01"))
    assert creds_ack is not None and creds_ack.command is None
    assert p.parse_ble_ack(h("37 00")) is None


def test_iter_frames_stream() -> None:
    buffer = bytearray(
        b"\x00\x01" + p.encode_frame(0x14, b"\x0a") + p.encode_frame(0x14, b"\x64")[:5]
    )
    frames = p.iter_frames(buffer)
    assert frames == [p.encode_frame(0x14, b"\x0a")]
    buffer.extend(p.encode_frame(0x14, b"\x64")[5:])
    (frame,) = p.iter_frames(buffer)
    progress = p.parse_firmware_progress(frame)
    assert progress.done and not progress.failed
    assert p.FirmwareProgress(101).failed
    assert buffer == bytearray()


def test_wifi_credentials_frame() -> None:
    frame = p.decode_frame(p.build_wifi_credentials("Net", "pw"))
    assert frame.command == 0x15
    assert frame.payload == b'{"pwd":"pw","ssid":"Net"}'


def test_mac_helpers() -> None:
    assert p.normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"
    with pytest.raises(ValueError):
        p.normalize_mac("aa:bb")
    assert p.default_name("aa:bb:cc:dd:0e:0f") == "WiseMirror-0E0F"
