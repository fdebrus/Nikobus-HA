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

    def __init__(self, tcp_client: SimpleTCPClient, hostname: str, ipport: str) -> None:
        """Initialize Nikobus API."""
        self.tcp_client = tcp_client
        self._hostname = hostname
        self._ipport = ipport
        self._autoConnect = True
        self.handlers = []

    @classmethod
    async def create(cls, tcp_client: SimpleTCPClient, hostname: str, ipport: str):
        """Initialize Nikobus."""
        instance = tcp_client(hostname, ipport)
        return instance

    async def get_data(self):
        """Retrieve data from the Nikobus system."""
        while True:
            try:
                response = await self.tcp_client.receive()
                _LOGGER.debug("Data received: %s", response)
            except Exception as e:
                print("An error occurred while receiving data:", e)

class UnauthorizedException(Exception):
    """Unauthorized user exception."""
