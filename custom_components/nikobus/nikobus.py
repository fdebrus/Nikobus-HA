"""Nikobus API"""
from __future__ import annotations

import logging
import select
import socket
from typing import Any, Final

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .const import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_NAME,
    DEFAULT_TIMEOUT,
)

__title__ = "Nikobus"
__version__ = "0.0.1"
__author__ = "Frederic Debrus"
__license__ = "MIT"

_LOGGER = logging.getLogger(__name__)

class NikobusBridge(entity):
    
    def __init__(self, host: str, port: str) -> None:
        """Initialize Nikobus Connection"""
        self.host = host
        self.port = port
        self.handlers = []

    def connect_bridge(self, host: str, port: str) -> Any:
        """Connect Bridge linked to Nikobus PC Link"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(DEFAULT_TIMEOUT)
            try:
                sock.connect((host, port))
                return sock
            except OSError as err:
                _LOGGER.error(
                    "Unable to connect to %s on port %s: %s",
                    host,
                    port,
                    err,
                )
            return

    def get(self, sock) -> Any:
        readable, _, _ = select.select([sock], [], [], DEFAULT_TIMEOUT)
        if not readable:
           _LOGGER.warning(
               (
                 "Timeout (%s second(s)) waiting for a response after "
                 "%s on port %s"
               ),
                 DEFAULT_TIMEOUT,
                 self._host,
                 self._port,
               )
           return

        value = sock.recv(DEFAULT_BUFFER_SIZE).decode()
        _LOGGER.info(value)
        return value
