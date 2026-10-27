"""Tests for the UDP and TCP transports against a simulated mirror."""

from __future__ import annotations

import pytest

from custom_components.wisemirror import firmware, transport
from custom_components.wisemirror.protocol import (
    QUERY_OLD_DEVICE,
    build_brightness,
    build_name,
    parse_name_reply,
)
from custom_components.wisemirror.transport import (
    WiseMirrorError,
    WiseMirrorRejected,
    WiseMirrorTimeout,
    WiseMirrorUdpClient,
    async_discover,
    async_provision_ap,
    async_start_firmware_update,
)

from .fake_mirror import MAC, FakeTcpMirror, start_fake_mirror

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "REPLY_TIMEOUT", 0.2)
    monkeypatch.setattr(transport, "RETRY_INTERVAL", 0.25)


async def test_request_and_reads() -> None:
    mirror, udp = await start_fake_mirror()
    try:
        client = WiseMirrorUdpClient("127.0.0.1", mirror.port)
        heartbeat = await client.heartbeat()
        assert heartbeat.temperature == 22
        assert heartbeat.device_name == "Bathroom"
        await client.request(build_brightness(86), timeout=0.2, interval=0.25)
        settings = await client.read_settings()
        assert settings.brightness == 86
        weather = await client.read_weather()
        assert weather.source == 255
        reply = await client.request(build_name("Hall"))
        assert parse_name_reply(reply).firmware == "V4.6.230601"
        assert mirror.state.name == "Hall"
    finally:
        udp.close()


async def test_request_retries() -> None:
    mirror, udp = await start_fake_mirror(drop=2)
    try:
        client = WiseMirrorUdpClient("127.0.0.1", mirror.port)
        await client.request(build_brightness(30), timeout=0.2, interval=0.25)
        assert len(mirror.received) == 3
        assert mirror.state.brightness == 30
    finally:
        udp.close()


async def test_request_timeout_and_heartbeat_single_try() -> None:
    mirror, udp = await start_fake_mirror(drop=10)
    try:
        client = WiseMirrorUdpClient("127.0.0.1", mirror.port)
        with pytest.raises(WiseMirrorTimeout):
            await client.request(build_brightness(30), timeout=0.1, interval=0.1)
        assert len(mirror.received) == 3
        mirror.received.clear()
        with pytest.raises(WiseMirrorTimeout):
            await client.request(build_brightness(30), tries=1, timeout=0.1)
        assert len(mirror.received) == 1
    finally:
        udp.close()


async def test_request_rejected() -> None:
    mirror, udp = await start_fake_mirror(reject={0x06})
    try:
        client = WiseMirrorUdpClient("127.0.0.1", mirror.port)
        with pytest.raises(WiseMirrorRejected):
            await client.request(build_brightness(30))
    finally:
        udp.close()


async def test_no_host() -> None:
    with pytest.raises(WiseMirrorTimeout):
        await WiseMirrorUdpClient("").request(build_brightness(30))


async def test_discover_unicast() -> None:
    mirror, udp = await start_fake_mirror()
    try:
        (reply,) = await async_discover(
            "127.0.0.1", port=mirror.port, source_port=0, timeout=0.5
        )
        assert reply.mac == MAC
        assert reply.model == "8J12"
        assert reply.version == "V4.6.230601"
        assert reply.humidity == 45
        (old,) = await async_discover(
            "127.0.0.1",
            query=QUERY_OLD_DEVICE,
            port=mirror.port,
            source_port=0,
            timeout=0.5,
        )
        assert old.flag == "1"
    finally:
        udp.close()


async def test_discover_nothing() -> None:
    mirror, udp = await start_fake_mirror(drop=10)
    try:
        assert (
            await async_discover(
                "127.0.0.1", port=mirror.port, source_port=0, timeout=0.2
            )
            == []
        )
    finally:
        udp.close()


async def test_provision_ap() -> None:
    tcp = FakeTcpMirror()
    await tcp.start()
    try:
        await async_provision_ap("HomeNet", "secret", "127.0.0.1", port=tcp.port)
        assert tcp.frames[0][1] == 0x15
        assert b'"ssid":"HomeNet"' in tcp.frames[0]
    finally:
        await tcp.stop()


async def test_provision_ap_unreachable() -> None:
    with pytest.raises(WiseMirrorTimeout):
        await async_provision_ap("x", "y", "127.0.0.1", port=1, timeout=1)


async def test_firmware_update() -> None:
    tcp = FakeTcpMirror()
    await tcp.start()
    seen: list[int] = []
    image = bytes(range(256)) * 100
    server = firmware.FirmwareServer(image, "8J12")
    port = await server.start(0)
    try:
        result = await async_start_firmware_update(
            "127.0.0.1",
            port,
            lambda progress: seen.append(progress.percent),
            port=tcp.port,
        )
    finally:
        await server.stop()
        await tcp.stop()
    assert result.done
    assert seen == [10, 50, 100]
    assert tcp.downloaded == image
    assert server.requests == ["/AST8J12.bin"]


async def test_firmware_update_failure() -> None:
    tcp = FakeTcpMirror(fail_update=True)
    await tcp.start()
    server = firmware.FirmwareServer(b"img", None)
    port = await server.start(0)
    try:
        with pytest.raises(WiseMirrorError):
            await async_start_firmware_update("127.0.0.1", port, port=tcp.port)
    finally:
        await server.stop()
        await tcp.stop()
