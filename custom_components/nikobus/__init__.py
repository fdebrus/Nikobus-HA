from homeassistant import config_entries, core
from homeassistant.components import binary_sensor, light, switch, sensor, select
from homeassistant.const import CONF_PORT

from .const import DOMAIN
from ./handler/NikobusPcLinkHandler import NikobusPcLinkHandler

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    await NikobusPcLinkHandler.async_setup(hass, config: Dict)
    return True
