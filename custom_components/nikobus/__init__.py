import asyncio
import serial

from homeassistant import config_entries, core
from homeassistant.components import binary_sensor, light, switch, sensor, select
from homeassistant.const import CONF_PORT

from .const import DOMAIN

async def async_setup(hass, config):
    return True
