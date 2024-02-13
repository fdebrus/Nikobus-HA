import logging
from typing import Any

from simple_socket.tcp_client import SimpleTCPClient

_LOGGER = logging.getLogger(__name__)

__title__ = "Nikobus"
__version__ = "0.0.0"
__author__ = "Frederic Debrus"
__license__ = "MIT"

class Nikobus:
    """Nikobus API."""

    def __init__(self, aioTCP_session: SimpleTCPClient, hostname: str, ipport: str) -> None:
        """Initialize Nikobus API."""
        self.aioTCP_session = aioTCP_session
        self._hostname = hostname
        self._ipport = ipport
        self._autoConnect = True
        self.handlers = []

    @classmethod
    async def create(cls, aioTCP_session: SimpleTCPClient, hostname: str, ipport: str):
        """Initialize Nikobus."""
        instance = aioTCP_session(hostname, ipport)
        return instance

    async def get_data(self):
        """Retrieve data from the Nikobus system."""
        try:
            data = await self.aioTCP_session.get_data()
            _LOGGER.debug("Data received: %s", data)
            return data
        except Exception as e:
            _LOGGER.error("Failed to retrieve data from Nikobus: %s", e)
            raise

class UnauthorizedException(Exception):
    """Unauthorized user exception."""
