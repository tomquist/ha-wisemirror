"""Fixtures for WiseMirror tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.wisemirror import discovery


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in every test."""
    return


@pytest.fixture(autouse=True)
def background_scan() -> Generator[AsyncMock]:
    """Keep the background discovery broadcast off the network."""
    with patch.object(discovery, "async_discover", AsyncMock(return_value=[])) as mock:
        yield mock
