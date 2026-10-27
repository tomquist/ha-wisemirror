"""Network transports: UDP discovery and control, TCP setup and updates."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import socket

from .protocol import (
    AP_HOST,
    CONTROL_PORT,
    DISCOVERY_PORT,
    DISCOVERY_SOURCE_PORT,
    QUERY_TEMPERATURE,
    STATUS_OK,
    TCP_PORT,
    Command,
    DiscoveryReply,
    FirmwareProgress,
    Heartbeat,
    Settings,
    WeatherSettings,
    build_firmware_update,
    build_heartbeat,
    build_read_settings,
    build_read_weather,
    build_wifi_credentials,
    iter_frames,
    parse_discovery_reply,
    parse_firmware_progress,
    parse_heartbeat,
    parse_settings,
    parse_weather,
)

_LOGGER = logging.getLogger(__name__)

REPLY_TIMEOUT = 0.8
RETRY_INTERVAL = 1.0
REQUEST_TRIES = 3
DISCOVERY_TIMEOUT = 2.0
BROADCAST = "255.255.255.255"

_DISCOVERY_LOCK = asyncio.Lock()


class WiseMirrorError(Exception):
    """Base error for mirror communication."""


class WiseMirrorTimeout(WiseMirrorError):
    """The mirror did not answer."""


class WiseMirrorRejected(WiseMirrorError):
    """The mirror answered with a status other than 0x01."""

    def __init__(self, command: int, status: int) -> None:
        """Store the rejected command and its status."""
        super().__init__(f"command 0x{command:02X} rejected (status 0x{status:02X})")
        self.command = command
        self.status = status


class _QueueProtocol(asyncio.DatagramProtocol):
    """Datagram protocol that queues every received packet."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait((data, addr))

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)


async def _receive(
    protocol: _QueueProtocol, deadline: float
) -> tuple[bytes, tuple[str, int]] | None:
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        return None
    try:
        async with asyncio.timeout(remaining):
            return await protocol.queue.get()
    except TimeoutError:
        return None


class WiseMirrorUdpClient:
    """Control client for UDP port 8001.

    One request is outstanding at a time. Each request is sent from a fresh
    socket, waits 800 ms for the reply and is retried every second.
    """

    def __init__(self, host: str, port: int = CONTROL_PORT) -> None:
        """Initialise the client."""
        self.host = host
        self.port = port
        self._lock = asyncio.Lock()

    async def request(
        self,
        frame: bytes,
        *,
        tries: int = REQUEST_TRIES,
        timeout: float = REPLY_TIMEOUT,
        interval: float = RETRY_INTERVAL,
    ) -> bytes:
        """Send a frame and return the mirror's accepted reply."""
        if not self.host:
            raise WiseMirrorTimeout("no host known")
        command = frame[1]
        async with self._lock:
            loop = asyncio.get_running_loop()
            transport, protocol = await loop.create_datagram_endpoint(
                _QueueProtocol, local_addr=("0.0.0.0", 0), family=socket.AF_INET
            )
            try:
                for attempt in range(tries):
                    started = loop.time()
                    _LOGGER.debug(
                        "-> %s:%s %s (try %s)",
                        self.host,
                        self.port,
                        frame.hex(" "),
                        attempt + 1,
                    )
                    try:
                        transport.sendto(frame, (self.host, self.port))
                    except OSError as err:
                        raise WiseMirrorError(f"send failed: {err}") from err
                    deadline = started + timeout
                    while (received := await _receive(protocol, deadline)) is not None:
                        data, _addr = received
                        _LOGGER.debug("<- %s %s", _addr[0], data.hex(" "))
                        if len(data) < 6 or data[1] != command:
                            continue
                        if data[5] != STATUS_OK:
                            raise WiseMirrorRejected(command, data[5])
                        return data
                    if attempt + 1 < tries:
                        await asyncio.sleep(max(0.0, started + interval - loop.time()))
            finally:
                transport.close()
        raise WiseMirrorTimeout(f"no reply to command 0x{command:02X} from {self.host}")

    async def heartbeat(self) -> Heartbeat:
        """Send 0x12 once and parse the reply."""
        return parse_heartbeat(await self.request(build_heartbeat(), tries=1))

    async def read_settings(self) -> Settings:
        """Send 0x10 and parse the settings block."""
        return parse_settings(await self.request(build_read_settings()))

    async def read_weather(self) -> WeatherSettings:
        """Send 0x16 and parse the weather block."""
        return parse_weather(await self.request(build_read_weather()))


def _discovery_socket(source_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", source_port))
    except OSError:
        _LOGGER.debug("UDP port %s busy, using a random source port", source_port)
        sock.bind(("", 0))
    sock.setblocking(False)
    return sock


async def async_discover(
    host: str = BROADCAST,
    *,
    query: str = QUERY_TEMPERATURE,
    timeout: float = DISCOVERY_TIMEOUT,
    source_port: int = DISCOVERY_SOURCE_PORT,
    port: int = DISCOVERY_PORT,
) -> list[DiscoveryReply]:
    """Send a discovery query and collect the replies.

    ``host`` is the broadcast address by default; a unicast address queries
    one mirror.
    """
    async with _DISCOVERY_LOCK:
        loop = asyncio.get_running_loop()
        sock = _discovery_socket(source_port)
        transport, protocol = await loop.create_datagram_endpoint(
            _QueueProtocol, sock=sock
        )
        found: dict[str, DiscoveryReply] = {}
        try:
            transport.sendto(query.encode("ascii"), (host, port))
            deadline = loop.time() + timeout
            while (received := await _receive(protocol, deadline)) is not None:
                data, addr = received
                if data == query.encode("ascii"):
                    continue
                _LOGGER.debug("Discovery reply from %s: %r", addr[0], data)
                if reply := parse_discovery_reply(data, addr[0]):
                    found.setdefault(reply.mac, reply)
                    if host != BROADCAST:
                        break
        except OSError as err:
            _LOGGER.debug("Discovery failed: %s", err)
        finally:
            transport.close()
    return list(found.values())


async def async_find_mirror(
    mac: str, host: str | None = None, *, timeout: float = DISCOVERY_TIMEOUT
) -> DiscoveryReply | None:
    """Find a mirror by MAC, trying its last known address first."""
    targets = [host, BROADCAST] if host else [BROADCAST]
    for target in targets:
        for reply in await async_discover(target, timeout=timeout):
            if reply.mac == mac:
                return reply
    return None


# --------------------------------------------------------------------------
# TCP 8408
# --------------------------------------------------------------------------


async def _read_frames(
    reader: asyncio.StreamReader, buffer: bytearray, timeout: float
) -> list[bytes]:
    async with asyncio.timeout(timeout):
        chunk = await reader.read(1024)
    if not chunk:
        raise WiseMirrorError("connection closed by mirror")
    buffer.extend(chunk)
    return iter_frames(buffer)


async def async_provision_ap(
    ssid: str,
    password: str,
    host: str = AP_HOST,
    *,
    port: int = TCP_PORT,
    timeout: float = 15.0,
) -> None:
    """Send Wi-Fi credentials to a mirror in AP mode (command 0x15)."""
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError) as err:
        raise WiseMirrorTimeout(f"cannot connect to {host}:{port}: {err}") from err
    try:
        writer.write(build_wifi_credentials(ssid, password))
        await writer.drain()
        buffer = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                frames = await _read_frames(reader, buffer, remaining)
            except TimeoutError:
                break
            for frame in frames:
                if frame[1] == Command.WIFI_CREDENTIALS:
                    return
        raise WiseMirrorTimeout("mirror did not confirm the Wi-Fi credentials")
    except OSError as err:
        raise WiseMirrorError(f"AP provisioning failed: {err}") from err
    finally:
        writer.close()


ProgressCallback = Callable[[FirmwareProgress], Awaitable[None] | None]


async def async_start_firmware_update(
    host: str,
    http_port: int,
    progress_callback: ProgressCallback | None = None,
    *,
    port: int = TCP_PORT,
    connect_timeout: float = 10.0,
    idle_timeout: float = 120.0,
) -> FirmwareProgress:
    """Send 0x13 over TCP 8408 and follow the 0x14 progress reports.

    The caller serves the image over HTTP on ``http_port``. Returns the final
    progress report; raises when the mirror reports a failure.
    """
    try:
        async with asyncio.timeout(connect_timeout):
            reader, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError) as err:
        raise WiseMirrorTimeout(f"cannot connect to {host}:{port}: {err}") from err
    try:
        writer.write(build_firmware_update(http_port))
        await writer.drain()
        buffer = bytearray()
        last: FirmwareProgress | None = None
        while True:
            try:
                frames = await _read_frames(reader, buffer, idle_timeout)
            except TimeoutError as err:
                raise WiseMirrorTimeout("firmware update stalled") from err
            except WiseMirrorError:
                if last is not None and last.done:
                    return last
                raise
            for frame in frames:
                if frame[1] != Command.FIRMWARE_PROGRESS:
                    _LOGGER.debug("Update: frame %s", frame.hex(" "))
                    continue
                last = parse_firmware_progress(frame)
                if progress_callback is not None:
                    result = progress_callback(last)
                    if asyncio.iscoroutine(result):
                        await result
                if last.failed:
                    raise WiseMirrorError(
                        f"mirror reported firmware update failure ({last.percent})"
                    )
                if last.done:
                    return last
    except OSError as err:
        raise WiseMirrorError(f"firmware update failed: {err}") from err
    finally:
        writer.close()
