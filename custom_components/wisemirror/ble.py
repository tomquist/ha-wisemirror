"""BLE GATT client for the 2M09.

Messages are cut into 20-byte chunks written to 0xAE01. The mirror
acknowledges each chunk with a notification on 0xAE02. Reading 0xAE01
returns the status block (settings plus weather).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import logging
import random

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from .protocol import (
    BLE_NOTIFY_UUID,
    BLE_WRITE_UUID,
    FRAME_END,
    BleAck,
    Settings,
    WeatherSettings,
    build_wifi_credentials_json,
    chunk_message,
    parse_ble_ack,
    parse_ble_status,
)
from .transport import WiseMirrorError, WiseMirrorTimeout

_LOGGER = logging.getLogger(__name__)

ACK_TIMEOUT = 5.0
CHUNK_RESENDS = 3


class WiseMirrorBleError(WiseMirrorError):
    """The mirror reported an error over BLE."""


def random_msg_id() -> int:
    """Random message id in 1-254, never 0x5A."""
    while (msg_id := random.randint(1, 254)) == FRAME_END:
        pass
    return msg_id


class WiseMirrorBleClient:
    """Connect-per-operation GATT client."""

    def __init__(
        self, device_getter: Callable[[], BLEDevice | None], name: str
    ) -> None:
        """Initialise with a callable that returns the current BLEDevice."""
        self._device_getter = device_getter
        self._name = name
        self._lock = asyncio.Lock()

    async def _connect(self) -> BleakClientWithServiceCache:
        device = self._device_getter()
        if device is None:
            raise WiseMirrorTimeout("mirror not reachable over Bluetooth")
        try:
            return await establish_connection(
                BleakClientWithServiceCache, device, self._name, max_attempts=3
            )
        except (BleakError, BleakNotFoundError, TimeoutError) as err:
            raise WiseMirrorTimeout(f"Bluetooth connection failed: {err}") from err

    async def send(self, message: bytes) -> BleAck:
        """Send a chunked message; return the acknowledgement of chunk 0."""
        async with self._lock:
            client = await self._connect()
            try:
                return await self._send(client, message)
            except BleakError as err:
                raise WiseMirrorError(f"Bluetooth write failed: {err}") from err
            finally:
                await client.disconnect()

    async def _send(
        self, client: BleakClientWithServiceCache, message: bytes
    ) -> BleAck:
        msg_id = random_msg_id()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _notify(_sender: object, data: bytearray) -> None:
            _LOGGER.debug("BLE <- %s", bytes(data).hex(" "))
            queue.put_nowait(bytes(data))

        await client.start_notify(BLE_NOTIFY_UUID, _notify)
        first_ack: BleAck | None = None
        try:
            for chunk in chunk_message(message, msg_id):
                ack = await self._send_chunk(client, queue, chunk)
                if first_ack is None:
                    first_ack = ack
        finally:
            with contextlib.suppress(BleakError):
                await client.stop_notify(BLE_NOTIFY_UUID)
        assert first_ack is not None
        return first_ack

    async def _send_chunk(
        self,
        client: BleakClientWithServiceCache,
        queue: asyncio.Queue[bytes],
        chunk: bytes,
    ) -> BleAck:
        msg_id, offset = chunk[0], chunk[2]
        loop = asyncio.get_running_loop()
        for _attempt in range(1 + CHUNK_RESENDS):
            _LOGGER.debug("BLE -> %s", chunk.hex(" "))
            await client.write_gatt_char(BLE_WRITE_UUID, chunk, response=True)
            deadline = loop.time() + ACK_TIMEOUT
            while (remaining := deadline - loop.time()) > 0:
                try:
                    async with asyncio.timeout(remaining):
                        data = await queue.get()
                except TimeoutError:
                    break
                ack = parse_ble_ack(data)
                if ack is None:
                    raise WiseMirrorBleError(
                        f"mirror reported an error: {data.hex(' ')}"
                    )
                if ack.msg_id == msg_id and ack.offset == offset:
                    return ack
        raise WiseMirrorTimeout("no Bluetooth acknowledgement")

    async def send_frame(self, frame: bytes) -> BleAck:
        """Send an A5 frame; check that chunk 0's ack echoes the command."""
        ack = await self.send(frame)
        if ack.command is not None and ack.command != frame[1]:
            raise WiseMirrorBleError(
                f"acknowledged command 0x{ack.command:02X}, expected 0x{frame[1]:02X}"
            )
        return ack

    async def send_wifi_credentials(self, ssid: str, password: str) -> BleAck:
        """Send bare-JSON Wi-Fi credentials."""
        return await self.send(build_wifi_credentials_json(ssid, password))

    async def read_status(self) -> tuple[Settings, WeatherSettings]:
        """Read the status block from 0xAE01."""
        async with self._lock:
            client = await self._connect()
            try:
                data = await client.read_gatt_char(BLE_WRITE_UUID)
            except BleakError as err:
                raise WiseMirrorError(f"Bluetooth read failed: {err}") from err
            finally:
                await client.disconnect()
        _LOGGER.debug("BLE status: %s", bytes(data).hex(" "))
        return parse_ble_status(bytes(data))
