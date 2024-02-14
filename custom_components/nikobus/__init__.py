"""The Nikobus integration."""

import asyncio
import logging

from homeassistant import config_entries, core
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DOMAIN
from .nikobus import Nikobus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    hostname = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)

    reader, writer = await Nikobus.connect_bridge(self, hostname=hostname, port=port)
    _LOGGER.debug("Nikobus connected: %s / %s", reader, writer)

    # Perform an initial check or command to verify that the connection is working (pseudo-code)
    # await api.verify_connection()

    # Store API instance for later use
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = writer

    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True
