import asyncio
import logging
import math
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import ClientResponseError, ClientSession

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

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.hostname, self.port)

        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        for command in commands:
            self.writer.write(command.encode())
            await self.writer.drain()

        data = await self.reader.read(64)
        _LOGGER.debug("Received response: %s", data.decode())

    async def read_data(self):
        if not self.reader:
            await self.connect()
        data = await self.reader.readuntil(b'\r')
        return data

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    def get_status_update(group):
        """Get the status update code for a given group."""
        return self.group["status_update"]

    async def is_on(self, address, channel) -> Any:
        """Return part from document."""
        return await self.getOutputState(address, channel, "")

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

    def calculate_group_output_number(self, channel):
        group_output_number = (int(channel) - 1) % 6
        return group_output_number

    def calculate_group_number(self, channel):
        group_number = math.floor((int(channel) + 5) / 6)
        return group_number

#########################################################

    async def setOutputState(self, address, channel, value):
        if not self.writer:
            await self.connect()

        group_number = self.calculate_group_number(channel)
        group_output_number = self.calculate_group_output_number(channel)

        if group_number == 1:
            command = self.make_pc_link_command(0x15, address, value + 'FF')
        elif group_number == 2:
            command = self.make_pc_link_command(0x16, address, value + 'FF')

        _LOGGER.debug("Nikobus Final Command: %s", command)
        self.writer.write((command + '\r').encode())

#########################################################

    async def getOutputState(self, address, channel):
        if not self.writer:
            await self.connect()

        group_number = self.calculate_group_number(channel)
        group_output_number = self.calculate_group_output_number(channel)

        if group == 1:
            cmd = self.make_pc_link_command(0x12, address)
        elif group == 2:
            cmd = self.make_pc_link_command(0x17, address)

        _LOGGER.debug("Nikobus Final Command Answer: %s", command)
        self.writer.write((command + '\r').encode())
        answer = await self.reader.read(64)

        crc1 = int(answer[-2:], 16)
        crc2 = self.calc_crc2(answer[:-2])
        if crc1 != crc2:
            _LOGGER.error("pc-link checksum error 1")
            
        # Remove leading and trailing characters
        answer = answer[3:-2]
            
            # Check nikobus checksum
        crc1 = int(answer[-4:], 16)
        crc2 = self.calc_crc1(answer[:-4])
        if crc1 != crc2:
            _LOGGER.error("pc-link checksum error 2")
            
        # Extract relevant part of answer
        answer = answer[4:16]

        _LOGGER.debug('ANSWER: %s', answer)

#########################################################
#########################################################

    def calcCRC1(self, data):
        _LOGGER.debug('CRC1 DATA %s',data)
        crc = 0xFFFF
        for j in range(len(data) // 2):
            crc ^= (int(data[j * 2: (j + 1) * 2], 16) << 8)
            for i in range(8):
                if (crc >> 15) & 1 != 0:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc = crc << 1
        return crc & 0xFFFF

    def calcCRC2(self, data):
        _LOGGER.debug('CRC2 DATA %s',data)
        crc = 0
        for char in data:
            crc ^= ord(char)
            for _ in range(8):
                if (crc & 0xFF) >> 7 != 0:
                    crc = crc << 1
                    crc ^= 0x99
                else:
                    crc = crc << 1
        return crc & 0xFF

    def appendCRC1(self,data):
        return data + self.intToHex(self.calcCRC1(data), 4)

    def appendCRC2(self,data):
        return data + self.intToHex(self.calcCRC2(data), 2)

    def make_pc_link_command(self, func, addr, args=None):
        addr_int = int(addr, 16)
        _LOGGER.debug('ADDR %s',addr)
        data = self.intToHex(func, 2) + self.intToHex((addr_int >> 0) & 0xFF, 2) + self.intToHex((addr_int >> 8) & 0xFF, 2)

        if args is not None:
            data += args
        return self.appendCRC2('$' + self.intToHex(len(data) + 10, 2) + self.appendCRC1(data))

    def intToHex(self, value, digits):
        return ('00000000' + format(value, 'x').upper())[-digits:]

    def hexToInt(self, value):
        return int(value, 16)

    def intToDec(self, value, digits):
        return ('00000000' + str(value)).upper()[-digits:]

    def decToInt(self, value):
        return int(value, 10)

class UnauthorizedException(Exception):
    """Exception for unauthorized access attempts."""


