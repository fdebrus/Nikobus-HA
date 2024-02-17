"""The Nikobus integration."""

import asyncio
import logging

from homeassistant import config_entries, core
from homeassistant.components import switch, light, cover
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

import aiohttp
from aiohttp import ClientResponseError, ClientSession

from .const import DOMAIN
from .nikobus import Nikobus
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN]

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    api = await Nikobus.create(async_get_clientsession(hass), entry.data.get(CONF_HOST), entry.data.get(CONF_PORT))
    _LOGGER.debug("Nikobus connected: %s", api)

    coordinator = NikobusDataCoordinator(hass, api)

    # Store API instance for later use
    hass.data.setdefault(DOMAIN, {})
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True
