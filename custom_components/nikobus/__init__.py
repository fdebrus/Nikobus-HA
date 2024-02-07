"""The Nikobus integration."""
from homeassistant import config_entries, core
from homeassistant.components import binary_sensor, light, switch, sensor, select
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator
from .nikobus import Nikobus

PLATFORMS = [binary_sensor.DOMAIN, light.DOMAIN, switch.DOMAIN, sensor.DOMAIN, cover.DOMAIN]

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Nikobus component."""
    bridge = await Nikobus.create(entry.data[CONF_HOST], entry.data[CONF_PORT])
    coordinator = NikobusDataCoordinator(hass, bridge)
    
    coordinator.data = await hass.async_add_executor_job(bridge.get_bridge, entry.data["bridge_id"])
    
    hass.async_add_executor_job(bridge.subscribe, entry.data["bridge_id"], coordinator.set_updated_data)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Async setup component."""
    return True
