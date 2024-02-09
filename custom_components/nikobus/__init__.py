import asyncio
import serial

from homeassistant import config_entries, core
from homeassistant.components import binary_sensor, light, switch, sensor, select
from homeassistant.const import CONF_PORT
from homeassistant.helpers import service
from homeassistant.const import (
    CONF_PORT,
    CONF_COMMAND,
)

from .const import DOMAIN

async def async_setup(hass, config):
    return True

async def async_setup(hass, config):
    async def async_send_command_to_serial_port(call):
        port = call.data[CONF_PORT]
        command = call.data[CONF_COMMAND]
        try:
            with serial.Serial(port) as ser:
                ser.write(command.encode())
        except serial.SerialException as exc:
            _LOGGER.error("Error communicating with serial port: %s", exc)
            return False

    hass.services.async_register(
        DOMAIN,
        "send_command_to_serial_port",
        async_send_command_to_serial_port,
        schema=service.SERVICE_SEND_COMMAND_TO_SERIAL_PORT,
    )

    return True

