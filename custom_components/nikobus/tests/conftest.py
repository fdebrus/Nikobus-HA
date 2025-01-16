# tests/conftest.py
import pytest
from unittest.mock import AsyncMock, patch
from homeassistant.setup import async_setup_component

@pytest.fixture
async def setup_nikobus(hass):
    """Set up the Nikobus integration for testing."""
    with patch("custom_components.nikobus.NikobusLibrary") as mock_library:
        mock_library.return_value = AsyncMock()
        await async_setup_component(hass, "nikobus", {
            "nikobus": {
                "host": "127.0.0.1",
                "port": 12345,
            }
        })
        await hass.async_block_till_done()
        return mock_library
