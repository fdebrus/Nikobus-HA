import asyncio
import logging
import threading
import voluptuous as vol

from enum import Enum
from typing import Callable, Optional
from serial import Serial, SerialException
from serial.tools.list_ports import comports

import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.entity import Entity

from .const import *

__title__ = "Nikobus"
__version__ = "0.0.1"
__author__ = "Frederic Debrus"
__license__ = "MIT"

_LOGGER = logging.getLogger(__name__)

class Nikobus:
    
    def __init__(host: str, port: str) -> None:
        """Initialize Nikobus API."""
        self.handlers = []

    @classmethod
    async def create(host: str, port: str):
        """Initialize Nikobus async."""
        bridge = await asyncio.open_connection(host, port)
        return bridge
    
    async def async_setup(hass, config):
        # Example: send data to the socket
        writer.write(b"Hello, TCP socket!\n")
        await writer.drain()

        # Example: read data from the socket
        data = await reader.read(100)
        _LOGGER.info("Received data from TCP socket: %s", data.decode())

        # Optionally, you can create entities or services based on the data received

        # Return True to indicate that the integration was successfully set up
        return True

class NikobusConnection:
    def __init__(self, port_name: str, process_data: Callable[[bytes], None]):
        self.port_name = port_name
        self.process_data = process_data
        self.serial_port = None

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def connect(self) -> None:
        if self.is_connected():
            return

        port_id = None
        for port in comports():
            if port.device == self.port_name:
                port_id = port
                break

        if port_id is None:
            raise SerialException(f"Port '{self.port_name}' is not known!")

        logger.info("Connecting to %s", self.port_name)

        try:
            serial_port = Serial(port_id.device, baudrate=9600, timeout=1)
            serial_port.flushInput()
            serial_port.flushOutput()
            self.serial_port = serial_port
            logger.info("Connected to %s", self.port_name)
        except SerialException as e:
            raise SerialException(f"Error connecting to port '{self.port_name}': {e}")

    def close(self) -> None:
        if self.serial_port is not None:
            self.serial_port.close()
            logger.debug("Closed serial port %s", self.port_name)

    def get_output_stream(self) -> Optional[Serial]:
        if self.is_connected():
            return self.serial_port
        return None

    def serial_event(self, event) -> None:
        if event.event_type != "DATA_AVAILABLE":
            return

        if self.serial_port is None:
            return

        try:
            while self.serial_port.in_waiting > 0:
                data = self.serial_port.read()
                self.process_data(data)
        except SerialException as e:
            logger.debug("Error reading from serial port %s: %s", self.port_name, e)

class NikobusCommand:
    class Result:
        def __init__(self, result: str, exception: Optional[Exception] = None):
            self._callable = lambda: result
            if exception:
                self._callable = lambda: None
                self.exception = exception

        def get(self) -> str:
            if hasattr(self, 'exception'):
                raise self.exception
            return self._callable()

    class ResponseHandler:
        def __init__(self, response_length: int, address_start: int, response_code: str,
                     result_consumer: Callable[[Result], None]):
            self.response_length = response_length
            self.address_start = address_start
            self.response_code = response_code
            self.result_consumer = result_consumer
            self.is_completed = False

        def complete(self, result: Result) -> bool:
            if self.is_completed:
                return False
            self.is_completed = True
            try:
                self.result_consumer(result)
            except Exception as e:
                logger.warning(f"Processing result {result} failed with {e}")
            return True

    def __init__(self, payload: str, response_length: Optional[int] = None, address_start: Optional[int] = None,
                 response_code: Optional[str] = None, result_consumer: Optional[Callable[[Result], None]] = None):
        self.payload = payload + '\r'
        if response_length is not None and address_start is not None and response_code is not None and result_consumer is not None:
            self.response_handler = self.ResponseHandler(response_length, address_start, response_code, result_consumer)
        else:
            self.response_handler = None

    def get_payload(self) -> str:
        return self.payload

    def get_response_handler(self) -> Optional[ResponseHandler]:
        return self.response_handler

class SwitchModuleCommandFactory:
    @staticmethod
    def create_read_command(address: str, group: str, result_consumer: Callable[[Result], None]) -> NikobusCommand:
        SwitchModuleCommandFactory._check_address(address)
        command_payload = append_crc2(f"$10{group.get_status_request()}{address}")
        return NikobusCommand(command_payload, 27, 3, "$1C", result_consumer)

    @staticmethod
    def create_write_command(address: str, group: str, value: str, result_consumer: Callable[[Result], None]) -> NikobusCommand:
        SwitchModuleCommandFactory._check_address(address)
        if len(value) != 12:
            raise ValueError(f"Value must have 12 chars but got '{value}'")
        payload = f"{group.get_status_update()}{address}{value}FF"
        return NikobusCommand(append_crc2(f"$1E{payload}"), 13, 5, "$0E", result_consumer)

    @staticmethod
    def _check_address(address: str) -> None:
        if len(address) != 4:
            raise ValueError(f"Address must have 4 chars but got '{address}'")

class SwitchModuleGroup(Enum):
    FIRST = ("12", "15", 1)
    SECOND = ("17", "16", 7)

    def __init__(self, status_request: str, status_update: str, offset: int):
        self.status_request = status_request
        self.status_update = status_update
        self.offset = offset

    def get_status_request(self) -> str:
        return self.status_request

    def get_status_update(self) -> str:
        return self.status_update

    def get_offset(self) -> int:
        return self.offset

    def get_count(self) -> int:
        return 6

    @staticmethod
    def map_from_channel(channel_uid: ChannelUID) -> 'SwitchModuleGroup':
        if not channel_uid.id_without_group.startswith(CHANNEL_OUTPUT_PREFIX):
            raise ValueError("Unexpected channel {}".format(channel_uid.id))
        channel_number = int(channel_uid.id_without_group[len(CHANNEL_OUTPUT_PREFIX):])
        return SwitchModuleGroup.map_from_channel_number(channel_number)

    @staticmethod
    def map_from_channel_number(channel_number: int) -> 'SwitchModuleGroup':
        max_value = SwitchModuleGroup.SECOND.offset + SwitchModuleGroup.SECOND.get_count()
        if not SwitchModuleGroup.FIRST.offset <= channel_number <= max_value:
            raise ValueError("Channel number should be between [{}, {}], but got {}".format(
                SwitchModuleGroup.FIRST.offset, max_value, channel_number))
        return SwitchModuleGroup.SECOND if channel_number >= SwitchModuleGroup.SECOND.offset else SwitchModuleGroup.FIRST


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
