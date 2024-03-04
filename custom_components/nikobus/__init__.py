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

# Define the platforms that the Nikobus integration will set up
PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, button.DOMAIN]

# Configuration key for the connection string
CONF_CONNECTION_STRING = "connection_string"

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up Nikobus from a config entry.

    This function initializes the Nikobus integration in Home Assistant,
    using the configuration entry provided by the user during the setup process.
    It creates an instance of the Nikobus API, sets up a data coordinator for fetching updates,
    and forwards the setup to the specified platforms (switch, light, cover, button).

    Parameters:
    - hass: The HomeAssistant instance.
    - entry: The configuration entry created by the user.

    Returns:
    - True if setup was successful, False otherwise.
    """

    # Retrieve the connection string from the configuration entry
    connection_string = entry.data.get(CONF_CONNECTION_STRING)

    # Initialize the Nikobus API with the provided connection string
    api = await Nikobus.create(hass, connection_string)
    if not api:
        # Log an error and return False if the API initialization fails
        _LOGGER.error("Failed to connect to the Nikobus system.")
        return False

    _LOGGER.debug("*****Nikobus connected*****")

    # Create a data coordinator for the Nikobus system
    coordinator = NikobusDataCoordinator(hass, api)
    # Store the coordinator in Home Assistant's data dictionary under the integration's domain
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Refresh the data from Nikobus for the first time
    await coordinator.async_config_entry_first_refresh()

    # Forward the setup process to each platform defined in PLATFORMS
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Create a background task to process incoming commands from Nikobus
    # This ensures that command processing doesn't block the setup process
    hass.loop.create_task(api.process_commands())

    return True
