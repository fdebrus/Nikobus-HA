import pytest
from unittest.mock import AsyncMock, patch
from homeassistant.setup import async_setup_component

# Mock configuration for the Nikobus integration
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

async def test_cover_initialization(hass, setup_nikobus):
    """Test that the cover entity is correctly initialized."""
    await setup_nikobus
    state = hass.states.get("cover.bureau_frederic_volet")
    assert state is not None, "Cover entity not found"
    assert state.state == "open", "Initial state should be 'open'"

async def test_cover_open(hass, setup_nikobus):
    """Test opening the cover."""
    await setup_nikobus
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.bureau_frederic_volet"}, blocking=True
    )
    state = hass.states.get("cover.bureau_frederic_volet")
    assert state.state == "open", "Cover state should be 'open' after calling open_cover"

async def test_cover_close(hass, setup_nikobus):
    """Test closing the cover."""
    await setup_nikobus
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": "cover.bureau_frederic_volet"}, blocking=True
    )
    state = hass.states.get("cover.bureau_frederic_volet")
    assert state.state == "closed", "Cover state should be 'closed' after calling close_cover"

async def test_cover_position(hass, setup_nikobus):
    """Test setting the cover position."""
    await setup_nikobus
    await hass.services.async_call(
        "cover", "set_cover_position", {
            "entity_id": "cover.bureau_frederic_volet",
            "position": 50
        },
        blocking=True
    )
    state = hass.states.get("cover.bureau_frederic_volet")
    assert state.attributes.get("current_position") == 50, "Position should be set to 50"

async def test_command_queue(hass, setup_nikobus):
    """Test the command queue processes commands correctly."""
    mock_queue = AsyncMock()
    with patch("custom_components.nikobus.queue_command", mock_queue):
        await setup_nikobus
        await hass.services.async_call(
            "cover", "set_cover_position", {
                "entity_id": "cover.bureau_frederic_volet",
                "position": 75
            },
            blocking=True
        )
        mock_queue.assert_called_once(), "queue_command should be called once for position update"

async def test_multiple_covers(hass, setup_nikobus):
    """Test handling multiple cover entities."""
    await setup_nikobus

    # Open one cover
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.bureau_frederic_volet"}, blocking=True
    )
    state1 = hass.states.get("cover.bureau_frederic_volet")
    assert state1.state == "open", "Cover 1 should be open"

    # Close another cover (example cover entity)
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": "cover.living_room"}, blocking=True
    )
    state2 = hass.states.get("cover.living_room")
    assert state2.state == "closed", "Cover 2 should be closed"
