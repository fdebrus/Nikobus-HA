import asyncio
import logging
import threading

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.entity import Entity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class Nikobus:

    async def async_setup(hass, config):
        """Set up the TCP socket integration."""
        conf = config[DOMAIN]
        host = CONF_HOST
        port = CONF_PORT

        # Establish connection to TCP socket
        reader, writer = await asyncio.open_connection(host, port)

        # Example: send data to the socket
        writer.write(b"Hello, TCP socket!\n")
        await writer.drain()

        # Example: read data from the socket
        data = await reader.read(100)
        _LOGGER.info("Received data from TCP socket: %s", data.decode())

        # Optionally, you can create entities or services based on the data received

        # Return True to indicate that the integration was successfully set up
        return True

def __int_to_hex(value, digits):
    hex_str = format(value, 'X')
    padded_hex = '0' * (8 - len(hex_str)) + hex_str
    return padded_hex[-digits:]

def __hex_to_int(value):
    return int(value, 16)
    
def __int_to_dec(value, digits):
    dec_str = str(value)
    padded_dec = '0' * (8 - len(dec_str)) + dec_str
    return padded_dec[-digits:]

def __dec_to_int(value):
    return int(value)

def send_command(self, command, callback):
    _LOGGER.debug('Nikobus.sendCommand() enter')
    _LOGGER.debug('command = ' + command)
    _LOGGER.debug('serial tx [' + command + '] (cr)')
    
    self.serial_port.write(command + '\r', write_callback)
    
    _LOGGER.debug('Nikobus.sendCommand() leave')

def serial_port_on_data(self, data):
    s = data.decode('utf-8')
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\r':
            _LOGGER.debug('serial rx [' + self.serial_rx_data + '] (cr)')
            self.emit('command', self.serial_rx_data)
            if self.get_answer_callback is not None:
                # xxx$0512$1CB6020000FF000000FFC2B316
                # xxx$0515$0EFFB6020030
                # xxx$0516$0EFFB6020030
                # xxx$0517$1CB60200FF0000000000D253EE
                # xxx$0519$0EFFB6020030
                line = self.serial_rx_data
                j = line.rfind('$')
                if j >= 5 and line[j - 5:j] == self.wait_command_ack:
                    line = line[j:]
                    # $1CB6020000FF000000FFC2B316
                    # $0EFFB6020030
                    _LOGGER.debug('answer received')
                    if self.get_answer_timeout is not None:
                        clearTimeout(self.get_answer_timeout)
                        self.get_answer_timeout = None
                    callback = self.get_answer_callback
                    self.get_answer_callback = None
                    callback(None, line)
            self.serial_rx_data = ''
        else:
            self.serial_rx_data += c
        i += 1

 def send_command_get_answer(self, command, timeout, callback):
    _LOGGER.debug('Nikobus.sendCommandGetAnswer() enter')
    _LOGGER.debug('command = ' + command + ', ' + 'timeout = ' + str(timeout))
    self.get_answer_callback = callback
    self.wait_command_ack = '$05' + command[3:5]  # $1012B6027576C9 => $0512
    _LOGGER.debug('serial tx [' + command + '] (cr)', 2)        
    self.log('Nikobus.sendCommandGetAnswer() exit', 9)

class MyTCPSocketEntity(Entity):
    """Representation of a TCP socket entity."""

    def __init__(self):
        """Initialize the entity."""
        self._state = None

    async def async_update(self):
        """Update the entity."""
        # This is where you can update the state of the entity based on data from the socket
        # You can also perform additional operations here, such as sending commands to the socket
        pass

    @property
    def name(self):
        """Return the name of the entity."""
        return "Nikobus PC Link"

    @property
    def state(self):
        """Return the state of the entity."""
        return self._state

    @property
    def should_poll(self):
        """Return False because entity pushes its state."""
        return False
