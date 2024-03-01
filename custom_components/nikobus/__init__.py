"""The Nikobus integration."""
import logging
import asyncio

from homeassistant import config_entries, core
from homeassistant.core import HomeAssistant
from homeassistant.components import switch, light, cover, button
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DOMAIN
from .nikobus import Nikobus
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, button.DOMAIN]

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up Nikobus from a config entry."""
    api = await Nikobus.create(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    if api:
        _LOGGER.debug("*****Nikobus connected*****")

    coordinator = NikobusDataCoordinator(hass, api)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Perform the first data refresh
    await coordinator.async_config_entry_first_refresh()

    # Forward the entry setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Process commands as a separate task to not block the setup
    hass.loop.create_task(api.process_commands())

    return True
