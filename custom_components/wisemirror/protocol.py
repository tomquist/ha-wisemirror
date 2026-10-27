"""WiseMirror wire protocol.

Pure-Python encoders and decoders for the ``A5 … 5A`` frame used on every
carrier (UDP, TCP and BLE), the BLE chunking layer, the BLE advertisement and
the ASCII discovery replies. Nothing in this module does I/O, so it can be
used and tested without Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum
import json
import re
from typing import Any
from urllib.parse import quote, unquote

FRAME_START = 0xA5
FRAME_END = 0x5A
FRAME_OVERHEAD = 8
MIN_FRAME_LENGTH = 9

SUB_FIELD = 0x00
STATUS_OK = 0x01

DISCOVERY_PORT = 8000
CONTROL_PORT = 8001
TCP_PORT = 8408
AP_HOST = "192.168.4.1"
DISCOVERY_SOURCE_PORT_NEW = 4025
DISCOVERY_SOURCE_PORT = 4026
ESPTOUCH_PORT = 7001
ESPTOUCH_CONFIRM_PORT = 18266
JOIN_ANNOUNCE_PORT = 18267
FIRMWARE_HTTP_PORT = 5000

QUERY_NEW_DEVICE = "Are you a new device that link to LEDWiFi"
QUERY_OLD_DEVICE = "Are you a old device that link to LEDWiFi"
QUERY_TEMPERATURE = "What is the temperature of LEDWifi"

SUPPORTED_MODELS = ("8J11", "8J12", "2K02", "2M09")
MODEL_2M09 = "2M09"
BLE_MODEL_CODES = {9: MODEL_2M09}

BLE_SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
BLE_WRITE_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
BLE_NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"
BLE_NAME_PREFIX = "WiseMirror_BLE"
BLE_NAME_SHORT = "WISE"
BLE_CHUNK_DATA = 17
BLE_MAX_MESSAGE = 255

# Readings at or above these temperatures mean "no reading".
TEMP_INVALID_C = 100
TEMP_INVALID_F = 212


class Command(IntEnum):
    """Command codes. Replies echo the request's code."""

    KEY_TONE = 0x01
    CLOCK_24H = 0x02
    DAY_FIRST = 0x03
    TEMP_UNIT = 0x04
    UNKNOWN_05 = 0x05
    BRIGHTNESS = 0x06
    UNKNOWN_07 = 0x07
    UNKNOWN_08 = 0x08
    UNKNOWN_09 = 0x09
    LOCATION = 0x0B
    SLEEP_MODE = 0x0C
    SLEEP_WINDOW = 0x0D
    SLEEP_BRIGHTNESS = 0x0E
    NAME = 0x0F
    READ_SETTINGS = 0x10
    UNKNOWN_11 = 0x11
    HEARTBEAT = 0x12
    FIRMWARE_UPDATE = 0x13
    FIRMWARE_PROGRESS = 0x14
    WIFI_CREDENTIALS = 0x15
    READ_WEATHER = 0x16
    WEATHER = 0x30
    WEEKDAY_LANGUAGE = 0x31


# Commands that have no BLE path (the BLE status read replaces the reads).
UDP_ONLY_COMMANDS = frozenset({Command.SLEEP_BRIGHTNESS, Command.HEARTBEAT})

BRIGHTNESS_PRESETS = {"low": 30, "medium": 50, "high": 86}
SLEEP_BRIGHTNESS_PRESETS = {"low": 10, "medium": 20, "high": 30}

WEATHER_FLAG_TWO_DAY = 0x01
WEATHER_FLAG_TODAY = 0x02

WEATHER_SOURCE_AUTO = 255
# Option key (shown as Auto, NO.1 … NO.5) -> wire value.
WEATHER_SOURCES = {
    "auto": 255,
    "no_1": 1,
    "no_2": 2,
    "no_3": 3,
    "no_4": 0,
    "no_5": 4,
}

# Firmware version byte (BCD read as decimal) above which the extended
# weather-source list is offered.
EXTENDED_WEATHER_MIN_VERSION = {"8J11": 25, "8J12": 43, "2K02": 22}


class ProtocolError(Exception):
    """A frame or message could not be decoded."""


# --------------------------------------------------------------------------
# Frame layer
# --------------------------------------------------------------------------


def checksum(data: bytes | bytearray) -> int:
    """Return the sum of ``data`` mod 256."""
    return sum(data) & 0xFF


def encode_frame(
    command: int,
    payload: bytes = b"\x00",
    *,
    sub_field: int = SUB_FIELD,
    status: int = STATUS_OK,
) -> bytes:
    """Encode one ``A5 … 5A`` frame."""
    length = len(payload)
    if length > 0xFFFF:
        raise ValueError("payload too long")
    body = bytes(
        [command & 0xFF, length >> 8, length & 0xFF, sub_field & 0xFF, status & 0xFF]
    ) + bytes(payload)
    return bytes([FRAME_START]) + body + bytes([checksum(body), FRAME_END])


@dataclass(frozen=True, slots=True)
class Frame:
    """A decoded frame. ``raw`` keeps the whole frame for offset-based reads."""

    command: int
    sub_field: int
    status: int
    payload: bytes
    raw: bytes

    @property
    def accepted(self) -> bool:
        """Return True when the mirror reported success."""
        return self.status == STATUS_OK


def decode_frame(data: bytes, *, verify_checksum: bool = True) -> Frame:
    """Decode a frame, checking markers, length and checksum."""
    if len(data) < MIN_FRAME_LENGTH:
        raise ProtocolError(f"frame too short ({len(data)} bytes)")
    if data[0] != FRAME_START:
        raise ProtocolError("missing start marker")
    length = (data[2] << 8) | data[3]
    end = 6 + length
    if len(data) < end + 2:
        raise ProtocolError("frame shorter than its length field")
    if data[end + 1] != FRAME_END:
        raise ProtocolError("missing end marker")
    if verify_checksum and checksum(data[1:end]) != data[end]:
        raise ProtocolError("checksum mismatch")
    raw = bytes(data[: end + 2])
    return Frame(
        command=data[1],
        sub_field=data[4],
        status=data[5],
        payload=raw[6:end],
        raw=raw,
    )


def is_success_reply(data: bytes, command: int) -> bool:
    """Success rule: byte 1 is the request's code, byte 5 is 0x01."""
    return len(data) >= 6 and data[1] == command and data[5] == STATUS_OK


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------


def _flag(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"


def build_key_tone(on: bool) -> bytes:
    """0x01: key tone on/off."""
    return encode_frame(Command.KEY_TONE, _flag(on))


def build_clock_24h(on: bool) -> bytes:
    """0x02: 1 = 24-hour clock."""
    return encode_frame(Command.CLOCK_24H, _flag(on))


def build_day_first(on: bool) -> bytes:
    """0x03: 1 = day before month."""
    return encode_frame(Command.DAY_FIRST, _flag(on))


def build_temp_unit(celsius: bool) -> bytes:
    """0x04: 1 = °C."""
    return encode_frame(Command.TEMP_UNIT, _flag(celsius))


def build_brightness(level: int) -> bytes:
    """0x06: display brightness."""
    return encode_frame(Command.BRIGHTNESS, bytes([_byte(level)]))


def build_location_payload(location: str) -> bytes:
    """Payload of 0x0B: 2-byte big-endian length and the UTF-8 string."""
    data = location.encode("utf-8")
    if len(data) > 0xFFFF - 2:
        raise ValueError("location too long")
    return len(data).to_bytes(2, "big") + data


def build_location(location: str) -> bytes:
    """0x0B: location string for the mirror's weather lookup."""
    return encode_frame(Command.LOCATION, build_location_payload(location))


def format_location(
    city: str, latitude: float, longitude: float, location_key: str = ""
) -> str:
    """Build ``<URL-encoded city>(<lat>,<lon>)(<key>)`` as the mirror expects."""
    return f"{quote(city, safe='')}({latitude:.2f},{longitude:.2f})({location_key})"


_LOCATION_RE = re.compile(
    r"^(?P<city>[^(]*)\((?P<lat>-?[\d.]+),(?P<lon>-?[\d.]+)\)(?:\((?P<key>[^)]*)\))?$"
)


@dataclass(frozen=True, slots=True)
class Location:
    """A parsed location string."""

    city: str
    latitude: float
    longitude: float
    location_key: str


def parse_location(value: str) -> Location | None:
    """Parse a location string; return None when it has another shape."""
    if not (match := _LOCATION_RE.match(value.strip())):
        return None
    try:
        return Location(
            city=unquote(match["city"]),
            latitude=float(match["lat"]),
            longitude=float(match["lon"]),
            location_key=match["key"] or "",
        )
    except ValueError:
        return None


def build_sleep_mode(on: bool) -> bytes:
    """0x0C: sleep mode on/off."""
    return encode_frame(Command.SLEEP_MODE, _flag(on))


def build_sleep_window(
    start_hour: int, start_minute: int, end_hour: int, end_minute: int
) -> bytes:
    """0x0D: sleep window, always start and end together, 24-hour."""
    for hour in (start_hour, end_hour):
        if not 0 <= hour <= 23:
            raise ValueError("hour must be 0-23")
    for minute in (start_minute, end_minute):
        if not 0 <= minute <= 59:
            raise ValueError("minute must be 0-59")
    return encode_frame(
        Command.SLEEP_WINDOW, bytes([start_hour, start_minute, end_hour, end_minute])
    )


def build_sleep_brightness(level: int) -> bytes:
    """0x0E: brightness while sleeping (UDP only)."""
    return encode_frame(Command.SLEEP_BRIGHTNESS, bytes([_byte(level)]))


def build_name(name: str) -> bytes:
    """0x0F: device name, 1-byte length and UTF-8."""
    data = name.encode("utf-8")
    if len(data) > 0xFF:
        raise ValueError("name too long")
    return encode_frame(Command.NAME, bytes([len(data)]) + data)


def build_read_settings() -> bytes:
    """0x10: read the settings block."""
    return encode_frame(Command.READ_SETTINGS, b"\x00")


def build_heartbeat() -> bytes:
    """0x12: heartbeat."""
    return encode_frame(Command.HEARTBEAT, b"\x00")


def build_firmware_update(http_port: int) -> bytes:
    """0x13: tell the mirror which HTTP port to fetch its image from."""
    if not 0 < http_port <= 0xFFFF:
        raise ValueError("invalid port")
    return encode_frame(Command.FIRMWARE_UPDATE, http_port.to_bytes(2, "big"))


def build_wifi_credentials_json(ssid: str, password: str) -> bytes:
    """Wi-Fi credentials as compact JSON."""
    return json.dumps(
        {"pwd": password, "ssid": ssid}, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def build_wifi_credentials(ssid: str, password: str) -> bytes:
    """0x15: Wi-Fi credentials frame for AP-mode setup over TCP."""
    return encode_frame(
        Command.WIFI_CREDENTIALS, build_wifi_credentials_json(ssid, password)
    )


def build_read_weather() -> bytes:
    """0x16: read the weather block."""
    return encode_frame(Command.READ_WEATHER, b"\x00")


def build_weather(flags: int, source: int) -> bytes:
    """0x30: weather flags and source, always together."""
    return encode_frame(Command.WEATHER, bytes([_byte(flags), _byte(source), 0, 0]))


def build_weekday_language(chinese: bool) -> bytes:
    """0x31: 0 = English, 1 = Chinese."""
    return encode_frame(Command.WEEKDAY_LANGUAGE, _flag(chinese))


def _byte(value: int) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError("value must be 0-255")
    return value


# --------------------------------------------------------------------------
# Reply parsers
# --------------------------------------------------------------------------


def decode_bcd_version(value: int) -> int:
    """Return the BCD version byte read as decimal (``0x46`` -> 46)."""
    return (value >> 4) * 10 + (value & 0x0F)


def format_firmware(version: int, year: int, month: int, day: int) -> str:
    """Format the firmware as ``V4.6.230601``.

    The version byte is BCD (``0x46`` is 4.6); the date bytes are plain
    binary (``17 06 01`` is 2023-06-01).
    """
    return f"V{version >> 4:X}.{version & 0x0F:X}.{year:02d}{month:02d}{day:02d}"


def version_from_string(version: str | None) -> int | None:
    """Return the version number from ``V4.6.230601`` (-> 46)."""
    if not version:
        return None
    match = re.match(r"^[Vv]?(\d+)\.(\d+)", version.strip())
    if not match:
        return None
    return int(match[1]) * 10 + int(match[2])


def _valid_humidity(value: int | None) -> int | None:
    """0 means the model has no humidity sensor; other values are clamped to 1-99."""
    if not value:
        return None
    return min(max(value, 1), 99)


def _valid_temperature(value: int, celsius: bool) -> int | None:
    limit = TEMP_INVALID_C if celsius else TEMP_INVALID_F
    if value >= limit:
        return None
    return value


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Reply to 0x12."""

    temperature: int | None
    celsius: bool
    version: int
    build_year: int
    build_month: int
    build_day: int
    humidity: int | None
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def firmware(self) -> str:
        """Firmware as ``V4.6.230601``."""
        return format_firmware(
            self.version, self.build_year, self.build_month, self.build_day
        )

    @property
    def version_number(self) -> int:
        """Version byte read as decimal."""
        return decode_bcd_version(self.version)

    @property
    def device_name(self) -> str | None:
        """Device name from the optional JSON."""
        name = self.info.get("mainDeviceName")
        return name if isinstance(name, str) and name else None


def parse_heartbeat(data: bytes) -> Heartbeat:
    """Parse a heartbeat reply frame."""
    frame = decode_frame(data)
    raw = frame.raw
    if frame.command != Command.HEARTBEAT:
        raise ProtocolError("not a heartbeat reply")
    if len(frame.payload) < 7:
        raise ProtocolError("heartbeat reply too short")
    celsius = raw[7] == 1
    temp = raw[6] - 0x100 if raw[6] >= 0x80 else raw[6]
    humidity = raw[12]
    info: dict[str, Any] = {}
    json_bytes = frame.payload[7:]
    if json_bytes:
        try:
            decoded = json.loads(json_bytes.decode("utf-8", errors="replace"))
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            info = decoded
    return Heartbeat(
        temperature=_valid_temperature(temp, celsius),
        celsius=celsius,
        version=raw[8],
        build_year=raw[9],
        build_month=raw[10],
        build_day=raw[11],
        humidity=_valid_humidity(humidity),
        info=info,
    )


@dataclass(frozen=True, slots=True)
class NameReply:
    """Reply to 0x0F."""

    version: int
    build_year: int
    build_month: int
    build_day: int

    @property
    def firmware(self) -> str:
        """Firmware as ``V4.6.230601``."""
        return format_firmware(
            self.version, self.build_year, self.build_month, self.build_day
        )


def parse_name_reply(data: bytes) -> NameReply | None:
    """Parse the firmware version carried in the 0x0F reply."""
    frame = decode_frame(data)
    if len(frame.payload) < 4:
        return None
    raw = frame.raw
    return NameReply(raw[6], raw[7], raw[8], raw[9])


@dataclass(frozen=True, slots=True)
class Alarm:
    """One of the five read-only alarms."""

    enabled: bool
    hour: int
    minute: int
    weekdays: int

    @property
    def weekday_list(self) -> list[int]:
        """Bits set in the weekday mask, bit 0 first."""
        return [bit for bit in range(7) if self.weekdays & (1 << bit)]


@dataclass(frozen=True, slots=True)
class WeatherSettings:
    """Weather flags and source (0x16 reply or BLE status block)."""

    flags: int
    source: int
    unknown: bytes = b"\x00\x00"
    location: str | None = None

    @property
    def two_day(self) -> bool:
        """Bit 0 of the flags."""
        return bool(self.flags & WEATHER_FLAG_TWO_DAY)

    @property
    def today(self) -> bool:
        """Bit 1 of the flags."""
        return bool(self.flags & WEATHER_FLAG_TODAY)


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings block (reply to 0x10)."""

    flags: int
    brightness: int
    brightness_duration: int
    time_date_mode: int
    alarms: tuple[Alarm, ...]
    sleep_on: bool
    sleep_start_hour: int
    sleep_start_minute: int
    sleep_end_hour: int
    sleep_end_minute: int
    sleep_brightness: int
    location: str

    @property
    def key_tone(self) -> bool:
        """Bit 0."""
        return bool(self.flags & 0x01)

    @property
    def clock_24h(self) -> bool:
        """Bit 1."""
        return bool(self.flags & 0x02)

    @property
    def day_first(self) -> bool:
        """Bit 2."""
        return bool(self.flags & 0x04)

    @property
    def celsius(self) -> bool:
        """Bit 3."""
        return bool(self.flags & 0x08)

    @property
    def always_bright(self) -> bool:
        """Bit 4 (read-only)."""
        return bool(self.flags & 0x10)

    @property
    def chinese_weekday(self) -> bool:
        """Bit 5."""
        return bool(self.flags & 0x20)

    def with_flag(self, bit: int, value: bool) -> Settings:
        """Return a copy with one flag bit changed."""
        flags = self.flags | bit if value else self.flags & ~bit
        return replace(self, flags=flags)


SETTINGS_FLAG_KEY_TONE = 0x01
SETTINGS_FLAG_24H = 0x02
SETTINGS_FLAG_DAY_FIRST = 0x04
SETTINGS_FLAG_CELSIUS = 0x08
SETTINGS_FLAG_ALWAYS_BRIGHT = 0x10
SETTINGS_FLAG_CHINESE = 0x20

SETTINGS_MIN_LENGTH = 38
BLE_STATUS_MIN_LENGTH = 42


def _parse_settings_raw(raw: bytes, location_offset: int) -> Settings:
    alarms = tuple(
        Alarm(
            enabled=bool(raw[10 + 4 * i]),
            hour=raw[11 + 4 * i],
            minute=raw[12 + 4 * i],
            weekdays=raw[13 + 4 * i],
        )
        for i in range(5)
    )
    length = (raw[location_offset] << 8) | raw[location_offset + 1]
    start = location_offset + 2
    location = raw[start : start + length].decode("utf-8", errors="replace")
    return Settings(
        flags=raw[6],
        brightness=raw[7],
        brightness_duration=raw[8],
        time_date_mode=raw[9],
        alarms=alarms,
        sleep_on=bool(raw[30]),
        sleep_start_hour=raw[31],
        sleep_start_minute=raw[32],
        sleep_end_hour=raw[33],
        sleep_end_minute=raw[34],
        sleep_brightness=raw[35],
        location=location,
    )


def parse_settings(data: bytes) -> Settings:
    """Parse the reply to 0x10."""
    frame = _decode_lenient(data)
    raw = frame.raw if frame else data
    if len(raw) < SETTINGS_MIN_LENGTH:
        raise ProtocolError("settings reply too short")
    return _parse_settings_raw(raw, 36)


def parse_ble_status(data: bytes) -> tuple[Settings, WeatherSettings]:
    """Parse the status block read from characteristic 0xAE01."""
    frame = _decode_lenient(data)
    raw = frame.raw if frame else data
    if len(raw) < BLE_STATUS_MIN_LENGTH:
        raise ProtocolError("BLE status block too short")
    settings = _parse_settings_raw(raw, 40)
    weather = WeatherSettings(
        flags=raw[36],
        source=raw[37],
        unknown=bytes(raw[38:40]),
        location=settings.location,
    )
    return settings, weather


def parse_weather(data: bytes) -> WeatherSettings:
    """Parse the reply to 0x16."""
    frame = _decode_lenient(data)
    raw = frame.raw if frame else data
    if len(raw) < 10:
        raise ProtocolError("weather reply too short")
    location = None
    if len(raw) >= 12:
        length = (raw[10] << 8) | raw[11]
        location = raw[12 : 12 + length].decode("utf-8", errors="replace")
    return WeatherSettings(
        flags=raw[6], source=raw[7], unknown=bytes(raw[8:10]), location=location
    )


def _decode_lenient(data: bytes) -> Frame | None:
    """Decode a frame, or return None for a block that only has the layout."""
    try:
        return decode_frame(data)
    except ProtocolError:
        if data[:1] != bytes([FRAME_START]):
            raise
        return None


@dataclass(frozen=True, slots=True)
class FirmwareProgress:
    """0x14 progress report sent by the mirror during an update."""

    percent: int

    @property
    def done(self) -> bool:
        """100 means finished."""
        return self.percent == 100

    @property
    def failed(self) -> bool:
        """Above 100 means failed."""
        return self.percent > 100


def parse_firmware_progress(data: bytes) -> FirmwareProgress:
    """Parse a 0x14 frame."""
    frame = decode_frame(data)
    if frame.command != Command.FIRMWARE_PROGRESS or len(frame.raw) < 9:
        raise ProtocolError("not a progress frame")
    return FirmwareProgress(frame.raw[6])


def iter_frames(buffer: bytearray) -> list[bytes]:
    """Pop every complete frame from a stream buffer (TCP)."""
    frames: list[bytes] = []
    while buffer:
        start = buffer.find(bytes([FRAME_START]))
        if start < 0:
            buffer.clear()
            break
        if start:
            del buffer[:start]
        if len(buffer) < 4:
            break
        total = ((buffer[2] << 8) | buffer[3]) + FRAME_OVERHEAD
        if len(buffer) < total:
            break
        candidate = bytes(buffer[:total])
        try:
            decode_frame(candidate)
        except ProtocolError:
            del buffer[:1]
            continue
        frames.append(candidate)
        del buffer[:total]
    return frames


# --------------------------------------------------------------------------
# Weather sources
# --------------------------------------------------------------------------


def weather_source_options(model: str | None, version: int | None) -> list[str]:
    """Return the weather-source option keys a mirror supports."""
    extended = model == MODEL_2M09 or (
        model in EXTENDED_WEATHER_MIN_VERSION
        and version is not None
        and version > EXTENDED_WEATHER_MIN_VERSION[model]
    )
    if not extended:
        return ["no_1", "no_2", "no_3"]
    options = ["auto", "no_1", "no_2", "no_3", "no_4"]
    if model == MODEL_2M09:
        options.append("no_5")
    return options


def weather_source_key(value: int) -> str | None:
    """Map a wire value to its option key."""
    for key, wire in WEATHER_SOURCES.items():
        if wire == value:
            return key
    return None


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveryReply:
    """One mirror's reply to a UDP discovery query."""

    model: str
    mac: str
    host: str
    word: str = ""
    temperature: int | None = None
    celsius: bool | None = None
    version: str | None = None
    humidity: int | None = None
    flag: str | None = None

    @property
    def default_name(self) -> str:
        """``WiseMirror-`` plus the last two MAC bytes in uppercase hex."""
        return default_name(self.mac)

    @property
    def version_number(self) -> int | None:
        """``V4.6.230601`` -> 46."""
        return version_from_string(self.version)


def normalize_mac(mac: str) -> str:
    """Return the MAC as lowercase hex with colons."""
    digits = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    if len(digits) != 12:
        raise ValueError(f"invalid MAC {mac!r}")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def default_name(mac: str) -> str:
    """Default display name derived from the MAC."""
    digits = normalize_mac(mac).replace(":", "").upper()
    return f"WiseMirror-{digits[-4:]}"


def match_model(value: str) -> str | None:
    """Return the supported model code contained in ``value``."""
    for model in SUPPORTED_MODELS:
        if model in value:
            return model
    return None


def _parse_unit(value: str) -> bool | None:
    value = value.strip().lower()
    if value in ("c", "1", "℃", "°c"):
        return True
    if value in ("f", "0", "℉", "°f"):
        return False
    return None


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        try:
            return round(float(value.strip()))
        except ValueError:
            return None


def parse_discovery_reply(
    data: bytes | str, source_host: str | None = None
) -> DiscoveryReply | None:
    """Parse a discovery reply; return None for anything that is not a mirror.

    A reply that reports IP ``0.0.0.0`` is dropped unless ``source_host`` (the
    address the datagram came from) is given, which is used instead.
    """
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = text.strip().strip("\x00").strip()
    if not text:
        return None
    humidity: int | None = None
    if "," in text:
        text, hum = text.rsplit(",", 1)
        humidity = _parse_int(hum)
    fields = text.split("+")
    head = fields[0].strip().split()
    if not head:
        return None
    model = match_model(head[-1]) or match_model(fields[0])
    if model is None:
        return None
    word = head[0] if len(head) > 1 else ""
    if len(fields) >= 6:
        temp = _parse_int(fields[1])
        celsius = _parse_unit(fields[2])
        version = fields[3].strip() or None
        mac_field, ip_field = fields[4], fields[5]
        flag = None
    elif len(fields) >= 4:
        temp = celsius = version = None
        flag = fields[1].strip()
        mac_field, ip_field = fields[-2], fields[-1]
    else:
        return None
    host = ip_field.strip()
    if not host or host == "0.0.0.0":
        if not source_host:
            return None
        host = source_host
    try:
        mac = normalize_mac(mac_field)
    except ValueError:
        return None
    if temp is not None and celsius is not None:
        temp = _valid_temperature(temp, celsius)
    return DiscoveryReply(
        model=model,
        mac=mac,
        host=host,
        word=word,
        temperature=temp,
        celsius=celsius,
        version=version,
        humidity=_valid_humidity(humidity),
        flag=flag,
    )


def format_discovery_reply(
    name: str,
    model: str,
    mac: str,
    host: str,
    *,
    temperature: int | None = None,
    unit: str = "C",
    version: str = "",
    humidity: int | None = None,
    flag: int = 0,
) -> str:
    """Build a reply in the firmware's format (used by tests and simulators)."""
    if temperature is None:
        return f"{name} {model}+{flag}+{mac}+{host}"
    return (
        f"{name} {model}+{temperature}+{unit}+{version}+{mac}+{host},"
        f"{humidity if humidity is not None else 0}"
    )


# --------------------------------------------------------------------------
# BLE advertisement
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Advertisement:
    """Manufacturer data of a 2M09 advertisement."""

    model: str | None
    model_code: int
    provisioned: bool
    celsius: bool
    temperature: int | None
    humidity: int
    mac: str

    @property
    def default_name(self) -> str:
        """Default display name."""
        return default_name(self.mac)


def is_wisemirror_ble_name(name: str | None) -> bool:
    """Return True for WiseMirror advertisement names."""
    return bool(name) and (name.startswith(BLE_NAME_PREFIX) or name == BLE_NAME_SHORT)


def parse_advertisement(manufacturer_data: bytes) -> Advertisement | None:
    """Parse the manufacturer data that follows the 2-byte company ID."""
    if len(manufacturer_data) < 11:
        return None
    celsius = bool(manufacturer_data[1] & 0x02)
    temp = int.from_bytes(manufacturer_data[2:4], "big", signed=True)
    mac = ":".join(f"{b:02x}" for b in manufacturer_data[5:11])
    return Advertisement(
        model=BLE_MODEL_CODES.get(manufacturer_data[0]),
        model_code=manufacturer_data[0],
        provisioned=bool(manufacturer_data[1] & 0x01),
        celsius=celsius,
        temperature=_valid_temperature(temp, celsius),
        humidity=manufacturer_data[4],
        mac=mac,
    )


def find_advertisement(manufacturer_data: dict[int, bytes]) -> Advertisement | None:
    """Pick the first manufacturer-data entry that parses."""
    for value in manufacturer_data.values():
        if adv := parse_advertisement(bytes(value)):
            return adv
    return None


# --------------------------------------------------------------------------
# BLE chunking
# --------------------------------------------------------------------------


def chunk_message(message: bytes, msg_id: int) -> list[bytes]:
    """Cut a message into ``[msg id][total][offset][data…]`` chunks."""
    if not 1 <= msg_id <= 254 or msg_id == FRAME_END:
        raise ValueError("msg id must be 1-254 and not 0x5A")
    total = len(message)
    if total > BLE_MAX_MESSAGE:
        raise ValueError("BLE message too long")
    return [
        bytes([msg_id, total, offset]) + message[offset : offset + BLE_CHUNK_DATA]
        for offset in range(0, max(total, 1), BLE_CHUNK_DATA)
    ]


@dataclass(frozen=True, slots=True)
class BleAck:
    """A notification acknowledging one chunk."""

    msg_id: int
    offset: int
    command: int | None
    data: bytes

    @property
    def is_frame_ack(self) -> bool:
        """True when the acknowledgement carries an A5 frame reply."""
        return self.command is not None


def parse_ble_ack(data: bytes) -> BleAck | None:
    """Parse a notification from 0xAE02; return None for an error (≤ 2 bytes)."""
    if len(data) <= 2:
        return None
    command = data[4] if len(data) > 4 and data[3] == FRAME_START else None
    return BleAck(msg_id=data[0], offset=data[2], command=command, data=bytes(data))
