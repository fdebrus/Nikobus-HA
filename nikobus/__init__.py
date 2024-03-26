"""The Nikobus integration."""

import logging
import asyncio

from homeassistant import config_entries, core
from homeassistant.core import HomeAssistant
from homeassistant.components import switch, light, cover, binary_sensor, button
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .nikobus import Nikobus
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, binary_sensor.DOMAIN, button.DOMAIN]

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:

    coordinator = NikobusDataCoordinator(hass, entry)

    await coordinator.connect()

    await coordinator.api.listen_for_events()

    await coordinator.api.command_handler()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def on_hass_stop(event):
    """Close connection when hass stops."""
    coordinator.api.close()
