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

async def test_light_initialization(hass, setup_nikobus):
    """Test that the light entity is correctly initialized."""
    await setup_nikobus
    state = hass.states.get("light.bureau_frederic_light")
    assert state is not None, "Light entity not found"
    assert state.state == "off", "Initial state should be 'off'"

async def test_light_turn_on(hass, setup_nikobus):
    """Test turning on the light."""
    await setup_nikobus
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.bureau_frederic_light"}, blocking=True
    )
    state = hass.states.get("light.bureau_frederic_light")
    assert state.state == "on", "Light state should be 'on' after calling turn_on"

async def test_light_turn_off(hass, setup_nikobus):
    """Test turning off the light."""
    await setup_nikobus
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.bureau_frederic_light"}, blocking=True
    )
    state = hass.states.get("light.bureau_frederic_light")
    assert state.state == "off", "Light state should be 'off' after calling turn_off"

async def test_switch_initialization(hass, setup_nikobus):
    """Test that the switch entity is correctly initialized."""
    await setup_nikobus
    state = hass.states.get("switch.bureau_frederic_switch")
    assert state is not None, "Switch entity not found"
    assert state.state == "off", "Initial state should be 'off'"

async def test_switch_turn_on(hass, setup_nikobus):
    """Test turning on the switch."""
    await setup_nikobus
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.bureau_frederic_switch"}, blocking=True
    )
    state = hass.states.get("switch.bureau_frederic_switch")
    assert state.state == "on", "Switch state should be 'on' after calling turn_on"

async def test_switch_turn_off(hass, setup_nikobus):
    """Test turning off the switch."""
    await setup_nikobus
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.bureau_frederic_switch"}, blocking=True
    )
    state = hass.states.get("switch.bureau_frederic_switch")
    assert state.state == "off", "Switch state should be 'off' after calling turn_off"
