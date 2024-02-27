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
    """Set up the Nikobus component."""
    api = await Nikobus.create(hass, entry.data.get(CONF_HOST), entry.data.get(CONF_PORT))
    if api:
        _LOGGER.debug("*****Nikobus connected*****")

    coordinator = NikobusDataCoordinator(hass, api)

    hass.data.setdefault(DOMAIN, {})
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    try:
        command_processor_task = hass.loop.create_task(
            api.process_commands()
        )
    except Exception as e:
        _LOGGER.debug(f"QUEUE TASK Failed to process commands: {e}")

    return True

async def async_unload_entry(hass: HomeAssistant, entry):
    """Unload a config entry."""
    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True
