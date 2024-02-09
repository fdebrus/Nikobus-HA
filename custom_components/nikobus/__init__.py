"""The Nikobus integration."""

from homeassistant import config_entries, core
from homeassistant.components import binary_sensor, light, switch, sensor, select
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DOMAIN
from .nikobus import Nikobus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)

    if host is None or port is None:
        _LOGGER.error("Missing required configuration data for Nikobus component")
        return False

    api = await Nikobus.create(hass, host, port)
    if api is None:
        _LOGGER.error("Failed to initialize Nikobus component")
        return False

    # Set up other platforms or entities if needed
    # Example: await hass.config_entries.async_forward_entry_setup(entry, 'light')

    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True

