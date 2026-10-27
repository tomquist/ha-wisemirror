"""Firmware update over the LAN.

The phone (here: Home Assistant) serves the image over HTTP and tells the
mirror the port with command 0x13 on TCP 8408. The mirror fetches
``/AST<MODEL>.bin`` and reports progress with 0x14 frames.
"""

from __future__ import annotations

import logging
import socket

from aiohttp import web

from .protocol import FIRMWARE_HTTP_PORT, FirmwareProgress
from .transport import ProgressCallback, async_start_firmware_update

_LOGGER = logging.getLogger(__name__)


class FirmwareServer:
    """Minimal HTTP server that hands one image to the mirror."""

    def __init__(self, image: bytes, model: str | None) -> None:
        """Initialise the server for one image."""
        self._image = image
        self._model = model
        self._runner: web.AppRunner | None = None
        self.port: int | None = None
        self.requests: list[str] = []

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(request.path)
        _LOGGER.debug("Firmware request from %s: %s", request.remote, request.path)
        if self._model and self._model.lower() not in request.path.lower():
            return web.Response(status=404)
        return web.Response(
            body=self._image,
            content_type="application/octet-stream",
            headers={"Connection": "close"},
        )

    async def start(self, port: int = FIRMWARE_HTTP_PORT) -> int:
        """Start on ``port``, or a random port above 1024 if it is taken."""
        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            _LOGGER.debug("HTTP port %s busy, using a random port", port)
            sock.bind(("0.0.0.0", 0))
        self.port = sock.getsockname()[1]
        try:
            await web.SockSite(self._runner, sock).start()
        except OSError:
            sock.close()
            await self.stop()
            raise
        return self.port

    async def stop(self) -> None:
        """Stop the server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


async def async_update_firmware(
    host: str,
    image: bytes,
    model: str | None,
    progress_callback: ProgressCallback | None = None,
    *,
    http_port: int = FIRMWARE_HTTP_PORT,
) -> FirmwareProgress:
    """Run a complete firmware update and return the final progress."""
    server = FirmwareServer(image, model)
    port = await server.start(http_port)
    try:
        return await async_start_firmware_update(host, port, progress_callback)
    finally:
        await server.stop()
