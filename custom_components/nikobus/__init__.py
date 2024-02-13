"""The Nikobus integration."""

import logging

from homeassistant import config_entries, core
from homeassistant.const import CONF_HOST, CONF_PORT

from simple_socket.tcp_client import SimpleTCPClient

from .const import DOMAIN
from .nikobus import Nikobus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    try:
        api = await Nikobus.create(SimpleTCPClient, entry.data[CONF_HOST], entry.data[CONF_PORT])
        return True
    except Exception as e:
        _LOGGER.error("Error setting up Nikobus component: %s", e)
        return False

    coordinator.data = await hass.async_add_executor_job(Nikobus.get_data)
    
    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True
