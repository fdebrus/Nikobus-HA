"""The Nikobus integration."""
import logging

from homeassistant import config_entries, core
from homeassistant.components import switch, light, cover
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .nikobus import Nikobus
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [switch.DOMAIN]

# light.DOMAIN, cover.DOMAIN]

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    api = await Nikobus.create(entry.data.get(CONF_HOST), entry.data.get(CONF_PORT))
    if api:
        _LOGGER.debug("*****Nikobus connected*****")

    coordinator = NikobusDataCoordinator(hass, api)

    hass.data.setdefault(DOMAIN, {})
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await NikobusDataCoordinator.initial_data_load(coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True
