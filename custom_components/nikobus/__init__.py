"""The Nikobus integration."""
import logging
import asyncio

from homeassistant import config_entries, core
from homeassistant.core import HomeAssistant
from homeassistant.components import switch, light, cover, button

from .const import DOMAIN
from .nikobus import Nikobus
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, button.DOMAIN]

CONF_CONNECTION_STRING="connection_string"

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up Nikobus from a config entry."""
    # Directly use the connection_string from the config entry
    connection_string = entry.data.get(CONF_CONNECTION_STRING)

    # Create the Nikobus API instance with the connection string
    api = await Nikobus.create(hass, connection_string)
    if not api:
        _LOGGER.error("Failed to connect to the Nikobus system.")
        return False

    _LOGGER.debug("*****Nikobus connected*****")

    # Create and store the data coordinator
    coordinator = NikobusDataCoordinator(hass, api)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Perform the first data refresh
    await coordinator.async_config_entry_first_refresh()

    # Forward the entry setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Process commands as a separate task to not block the setup
    hass.loop.create_task(api.process_commands())

    return True
