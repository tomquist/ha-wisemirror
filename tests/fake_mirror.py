"""A simulated mirror speaking the UDP and TCP protocol on localhost."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json

from custom_components.wisemirror.protocol import (
    Command,
    decode_frame,
    encode_frame,
    format_discovery_reply,
    iter_frames,
)

MAC = "aa:bb:cc:dd:ee:ff"


def heartbeat_reply(
    temp: int = 22,
    celsius: bool = True,
    version: int = 0x46,
    date: tuple[int, int, int] = (0x17, 0x06, 0x01),
    humidity: int = 45,
    info: dict | None = None,
) -> bytes:
    """Build a heartbeat reply as the 8J12 firmware does."""
    payload = bytes([temp & 0xFF, 1 if celsius else 0, version, *date, humidity])
    if info is not None:
        payload += json.dumps(info).encode()
    return encode_frame(Command.HEARTBEAT, payload)


@dataclass
class MirrorState:
    """Mutable state of the simulated mirror."""

    flags: int = 0b0000_1010
    brightness: int = 50
    brightness_duration: int = 0
    time_date_mode: int = 0
    alarms: list[tuple[int, int, int, int]] = field(
        default_factory=lambda: [
            (1, 7, 30, 0x1F),
            (0, 0, 0, 0),
            (0, 0, 0, 0),
            (0, 0, 0, 0),
            (0, 0, 0, 0),
        ]
    )
    sleep: list[int] = field(default_factory=lambda: [1, 23, 0, 6, 30, 10])
    location: str = "Berlin(52.52,13.40)()"
    weather_flags: int = 0x03
    weather_source: int = 255
    name: str = "Bathroom"
    temp: int = 22
    humidity: int = 45

    def settings_payload(self, ble: bool = False) -> bytes:
        loc = self.location.encode()
        payload = bytes(
            [self.flags, self.brightness, self.brightness_duration, self.time_date_mode]
        )
        for alarm in self.alarms:
            payload += bytes(alarm)
        payload += bytes(self.sleep)
        if ble:
            payload += bytes([self.weather_flags, self.weather_source, 0, 0])
        payload += len(loc).to_bytes(2, "big") + loc
        return payload

    def weather_payload(self) -> bytes:
        loc = self.location.encode()
        return (
            bytes([self.weather_flags, self.weather_source, 0, 0])
            + len(loc).to_bytes(2, "big")
            + loc
        )

    def apply(self, command: int, payload: bytes) -> bytes:
        """Apply a command and return the reply payload."""
        bit = {
            Command.KEY_TONE: 0,
            Command.CLOCK_24H: 1,
            Command.DAY_FIRST: 2,
            Command.TEMP_UNIT: 3,
            Command.WEEKDAY_LANGUAGE: 5,
        }
        if command in bit:
            mask = 1 << bit[command]
            self.flags = self.flags | mask if payload[0] else self.flags & ~mask
        elif command == Command.BRIGHTNESS:
            self.brightness = payload[0]
        elif command == Command.SLEEP_MODE:
            self.sleep[0] = payload[0]
        elif command == Command.SLEEP_WINDOW:
            self.sleep[1:5] = list(payload[:4])
        elif command == Command.SLEEP_BRIGHTNESS:
            self.sleep[5] = payload[0]
        elif command == Command.LOCATION:
            length = int.from_bytes(payload[:2], "big")
            self.location = payload[2 : 2 + length].decode()
        elif command == Command.NAME:
            self.name = payload[1 : 1 + payload[0]].decode()
            return bytes([0x46, 0x17, 0x06, 0x01])
        elif command == Command.WEATHER:
            self.weather_flags, self.weather_source = payload[0], payload[1]
        elif command == Command.READ_SETTINGS:
            return self.settings_payload()
        elif command == Command.READ_WEATHER:
            return self.weather_payload()
        return b"\x00"


class FakeMirror(asyncio.DatagramProtocol):
    """UDP control (and discovery) endpoint."""

    def __init__(
        self,
        state: MirrorState | None = None,
        *,
        drop: int = 0,
        reject: set[int] | None = None,
        heartbeat: bool = True,
        probe_host: str = "127.0.0.1",
    ) -> None:
        self.state = state or MirrorState()
        self.heartbeat = heartbeat
        self.probe_host = probe_host
        self.received: list[bytes] = []
        self.drop = drop
        self.reject = reject or set()
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0

    def connection_made(self, transport) -> None:
        self.transport = transport
        self.port = transport.get_extra_info("sockname")[1]

    def datagram_received(self, data: bytes, addr) -> None:
        self.received.append(data)
        if self.drop:
            self.drop -= 1
            return
        if data.startswith(b"What is the temperature"):
            reply = format_discovery_reply(
                "I'm",
                "8J12",
                MAC,
                self.probe_host,
                temperature=self.state.temp,
                unit="C",
                version="V4.6.230601",
                humidity=self.state.humidity,
            )
            self.transport.sendto(reply.encode(), addr)
            return
        if data.startswith(b"Are you a"):
            flag = 0 if b"new" in data else 1
            self.transport.sendto(
                format_discovery_reply(
                    "LEDWifi", "8J12", MAC, "127.0.0.1", flag=flag
                ).encode(),
                addr,
            )
            return
        frame = decode_frame(data)
        if frame.command == Command.HEARTBEAT and not self.heartbeat:
            return
        if frame.command in self.reject:
            self.transport.sendto(
                encode_frame(frame.command, b"\x00", status=0x00), addr
            )
            return
        if frame.command == Command.HEARTBEAT:
            reply = heartbeat_reply(
                temp=self.state.temp,
                humidity=self.state.humidity,
                info={"mainDeviceName": self.state.name, "unit": "c"},
            )
        else:
            reply = encode_frame(
                frame.command, self.state.apply(frame.command, frame.payload)
            )
        self.transport.sendto(reply, addr)


async def start_fake_mirror(**kwargs) -> tuple[FakeMirror, asyncio.DatagramTransport]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: FakeMirror(**kwargs), local_addr=("127.0.0.1", 0)
    )
    return protocol, transport


class FakeTcpMirror:
    """TCP 8408 endpoint: Wi-Fi credentials and firmware update."""

    def __init__(self, fail_update: bool = False) -> None:
        self.frames: list[bytes] = []
        self.downloaded: bytes | None = None
        self.path: str | None = None
        self.fail_update = fail_update
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        buffer = bytearray()
        while not (frames := iter_frames(buffer)):
            chunk = await reader.read(1024)
            if not chunk:
                return
            buffer.extend(chunk)
        frame = decode_frame(frames[0])
        self.frames.append(frames[0])
        if frame.command == Command.WIFI_CREDENTIALS:
            writer.write(encode_frame(Command.WIFI_CREDENTIALS, b"\x00"))
        elif frame.command == Command.FIRMWARE_UPDATE:
            port = int.from_bytes(frame.payload, "big")
            peer = writer.get_extra_info("peername")[0]
            r, w = await asyncio.open_connection(peer, port)
            self.path = "/AST8J12.bin"
            w.write(f"GET {self.path} HTTP/1.0\r\n\r\n".encode())
            await w.drain()
            response = await r.read()
            w.close()
            self.downloaded = response.split(b"\r\n\r\n", 1)[1]
            for percent in (10, 50, 101 if self.fail_update else 100):
                writer.write(encode_frame(Command.FIRMWARE_PROGRESS, bytes([percent])))
                await writer.drain()
        await writer.drain()
        writer.close()


def ble_device(address: str, name: str):
    """BLEDevice across bleak versions (older ones also take an RSSI)."""
    from bleak.backends.device import BLEDevice

    try:
        return BLEDevice(address, name, None)
    except TypeError:
        return BLEDevice(address, name, None, -60)
