"""Tests for the BLE chunking client with a fake GATT peer."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from custom_components.wisemirror import ble
from custom_components.wisemirror.ble import WiseMirrorBleClient, WiseMirrorBleError
from custom_components.wisemirror.protocol import build_name, encode_frame
from custom_components.wisemirror.transport import WiseMirrorTimeout

from .fake_mirror import MirrorState


class FakeGattClient:
    """Acknowledges every chunk like the mirror does."""

    def __init__(
        self, *, drop_first: int = 0, error: bool = False, echo: bool = False
    ) -> None:
        self.writes: list[bytes] = []
        self.callback = None
        self.drop_first = drop_first
        self.error = error
        self.echo = echo
        self.disconnected = False

    async def start_notify(self, uuid, callback) -> None:
        self.callback = callback

    async def stop_notify(self, uuid) -> None:
        self.callback = None

    async def write_gatt_char(self, uuid, data, response=True) -> None:
        self.writes.append(bytes(data))
        if self.drop_first:
            self.drop_first -= 1
            return
        if self.error:
            self.callback(None, bytearray(b"\x00\x00"))
            return
        if self.echo:
            # Acknowledge by echoing the whole chunk back.
            self.callback(None, bytearray(data))
            return
        ack = bytes(data[:3])
        if data[2] == 0 and data[3] == 0xA5:
            ack += bytes([0xA5, data[4], 0x01])
        else:
            ack += b"\x01"
        self.callback(None, bytearray(ack))

    async def read_gatt_char(self, uuid) -> bytearray:
        return bytearray(encode_frame(0x10, MirrorState().settings_payload(ble=True)))

    async def disconnect(self) -> None:
        self.disconnected = True


def _client(fake: FakeGattClient) -> tuple[WiseMirrorBleClient, object]:
    async def _connect(*args, **kwargs):
        return fake

    return WiseMirrorBleClient(lambda: object(), "test"), patch.object(
        ble, "establish_connection", _connect
    )


async def test_send_frame_chunks_and_acks() -> None:
    fake = FakeGattClient()
    client, patcher = _client(fake)
    with patcher:
        ack = await client.send_frame(build_name("Bathroom mirror"))
    assert ack.command == 0x0F
    assert len(fake.writes) == 2
    assert fake.writes[0][1:3] == b"\x18\x00"
    assert fake.writes[1][1:3] == b"\x18\x11"
    assert fake.writes[0][0] == fake.writes[1][0] != 0x5A
    assert fake.disconnected


async def test_wifi_credentials() -> None:
    fake = FakeGattClient()
    client, patcher = _client(fake)
    with patcher:
        ack = await client.send_wifi_credentials("HomeNet", "secret123")
    assert ack.command is None
    assert (
        b"".join(w[3:] for w in fake.writes) == b'{"pwd":"secret123","ssid":"HomeNet"}'
    )


async def test_resend_on_missing_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ble, "ACK_TIMEOUT", 0.05)
    fake = FakeGattClient(drop_first=2)
    client, patcher = _client(fake)
    with patcher:
        await client.send_frame(build_name("x"))
    assert len(fake.writes) == 3

    fake = FakeGattClient(drop_first=10)
    client, patcher = _client(fake)
    with patcher, pytest.raises(WiseMirrorTimeout):
        await client.send_frame(build_name("x"))
    assert len(fake.writes) == 4


async def test_error_notification() -> None:
    fake = FakeGattClient(error=True)
    client, patcher = _client(fake)
    with patcher, pytest.raises(WiseMirrorBleError):
        await client.send_frame(build_name("x"))


async def test_read_status() -> None:
    fake = FakeGattClient()
    client, patcher = _client(fake)
    with patcher:
        settings, weather = await client.read_status()
    assert settings.location == "Berlin(52.52,13.40)()"
    assert weather.source == 255


async def test_no_device() -> None:
    client = WiseMirrorBleClient(lambda: None, "test")
    with pytest.raises(WiseMirrorTimeout):
        await client.read_status()


def test_random_msg_id() -> None:
    ids = {ble.random_msg_id() for _ in range(2000)}
    assert 0x5A not in ids and 0 not in ids and 255 not in ids


async def test_wifi_credentials_stay_out_of_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The password must not reach the debug log, in either direction."""
    password = "s3cret-Passw0rd"
    fake = FakeGattClient(echo=True)
    client, patcher = _client(fake)
    caplog.set_level(logging.DEBUG, logger="custom_components.wisemirror")
    with patcher:
        await client.send_wifi_credentials("HomeNet", password)

    log = caplog.text
    secret = password.encode()
    assert password not in log
    # Each chunk carries at most 17 bytes, so look for every 3-byte piece of
    # the password in the hex forms the log could use.
    for start in range(len(secret) - 2):
        piece = secret[start : start + 3]
        assert piece.hex(" ") not in log
        assert piece.hex() not in log
    assert "payload not logged" in log
    # The credentials were still sent in full.
    sent = b"".join(write[3:] for write in fake.writes)
    assert password.encode() in sent


async def test_frames_are_still_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Ordinary commands keep their hex dump for debugging."""
    fake = FakeGattClient()
    client, patcher = _client(fake)
    caplog.set_level(logging.DEBUG, logger="custom_components.wisemirror")
    with patcher:
        await client.send_frame(build_name("x"))
    assert "BLE -> " in caplog.text
    assert "a5 0f" in caplog.text
