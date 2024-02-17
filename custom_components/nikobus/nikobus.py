""" Nikobus API """

import logging
import asyncio
from typing import Any, Callable, Optional
from aiohttp import ClientResponseError, ClientSession

from .helpers import MakePcLinkCommand, CalculateGroupOutputNumber, CalculateGroupNumber

_LOGGER = logging.getLogger(__name__)

__title__ = "Nikobus"
__version__ = "0.0.0"
__author__ = "Frederic Debrus"
__license__ = "MIT"

class Nikobus:
    """Nikobus API."""

    def __init__(self,  aiohttp_session: ClientSession, hostname: str, port: int) -> None:
        """Initialize Nikobus API."""
        self.hostname = hostname
        self.port = port
        self.aiohttp_session = aiohttp_session
        self.reader = None
        self.writer = None

    @classmethod
    async def create(cls, aiohttp_session: ClientSession, hostname: str, port: str):
        """Initialize Nikobus async."""
        instance = cls(aiohttp_session, hostname, port)
        await instance.connect()
        return instance

    def get_status_update(group):
        """Get the status update code for a given group."""
        return self.group["status_update"]

    def getState(self, address, channel) -> Any:
        """Return part from document."""
        return self.getOutputState(address, channel)

    async def turn_on_switch(self, address, channel) -> None:
        """Turn on switch."""
        await self.setOutputState(address, channel, "FF")

    async def turn_off_switch(self, address, channel) -> None:
        """Turn off switch."""
        await self.setOutputState(address, channel, "00")

    async def turn_on_light(self, address, channel) -> None:
        """Turn on light."""
        await self.setOutputState(address, channel, "FF")

    async def turn_off_light(self, address, channel) -> None:
        """Turn off light."""
        await self.setOutputState(address, channel, "00")

    async def open_cover(self, address, channel):
        """Open the cover."""
        pass

    async def async_close_cover(self, address, channel):
        """Close the cover."""
        pass

    async def async_stop_cover(self, address, channel):
        """Stop the cover."""
        pass

    async def get_cover_state(self, address, channel):
        """Update the state of the cover."""
        pass

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.hostname, self.port)

        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        for command in commands:
            self.writer.write(command.encode())
            await self.writer.drain()

        data = await self.reader.read(64)
        _LOGGER.debug("Received response: %s", data.decode())

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def setOutputState(self, address, channel, value):
        if not self.writer:
            await self.connect()

        group_number = CalculateGroupNumber(channel)
        group_output_number = CalculateGroupOutputNumber(channel)

        if group_number == 1:
            command = MakePcLinkCommand(0x15, address, value + 'FF')
        elif group_number == 2:
            command = MakePcLinkCommand(0x16, address, value + 'FF')

        _LOGGER.debug("Nikobus Final setOutputState: %s", command)
        self.writer.write((command + '\r').encode())

    async def getOutputState(self, address, channel):
        if not self.writer:
            self.connect()

        group_number = CalculateGroupNumber(channel)
        group_output_number = CalculateGroupOutputNumber(channel)

        if group_number == 1:
            command = MakePcLinkCommand(0x12, address)
        elif group_number == 2:
            command = MakePcLinkCommand(0x17, address)

        _LOGGER.debug("Nikobus Final getOutputState: %s", command)

        answer = await self._send_command_answer(self.writer, self.reader, command)

        _LOGGER.debug('FIRST ANSWER: %s', answer)

        #crc1 = int(answer[-2:], 16)
        #crc2 = self.calc_crc2(answer[:-2])
        #if crc1 != crc2:
        #_LOGGER.error("pc-link checksum error 1")
            
        # Remove leading and trailing characters
        answer = answer[3:-2]
            
        # Check nikobus checksum
        #crc1 = int(answer[-4:], 16)
        #crc2 = self.calc_crc1(answer[:-4])
        #if crc1 != crc2:
        #_LOGGER.error("pc-link checksum error 2")
            
        # Extract relevant part of answer
        answer = answer[4:16]

        _LOGGER.debug('FILTER ANSWER: %s', answer)

    async def _async_send_command_and_get_answer(self, writer, reader, command):
        writer.write((command + '\r').encode())
        await writer.drain()
        answer = await reader.read(64)
        return answer

    async def _send_command_answer(self, writer, reader, command):
        if asyncio.get_running_loop() is not None:
            answer = await self._async_send_command_and_get_answer(writer, reader, command)
        else:
            async with async_timeout.timeout(10):
                answer = await asyncio.get_event_loop().run_in_executor(None, lambda: self._async_send_command_and_get_answer(writer, reader, command))
        return answer


class UnauthorizedException(Exception):
    """Exception for unauthorized access attempts."""
