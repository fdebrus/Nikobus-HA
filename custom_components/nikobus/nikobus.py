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
    CONF_BUFFER_SIZE,
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
        self.update()

    def update(self, host: str, port: str) -> None:
        """Get the latest value."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._config[CONF_TIMEOUT])
            try:
                sock.connect((host, port))
            except OSError as err:
                _LOGGER.error(
                    "Unable to connect to %s on port %s: %s",
                    host,
                    port,
                    err,
                )
                return

            readable, _, _ = select.select([sock], [], [], self._config[CONF_TIMEOUT])
            if not readable:
                _LOGGER.warning(
                    (
                        "Timeout (%s second(s)) waiting for a response after "
                        "sending %r to %s on port %s"
                    ),
                    self._config[CONF_TIMEOUT],
                    self._config[CONF_PAYLOAD],
                    self._config[CONF_HOST],
                    self._config[CONF_PORT],
                )
                return

            value = sock.recv(self._config[CONF_BUFFER_SIZE]).decode()

        value_template = self._config[CONF_VALUE_TEMPLATE]
        if value_template is not None:
            try:
                self._state = value_template.render(parse_result=False, value=value)
                return
            except TemplateError:
                _LOGGER.error(
                    "Unable to render template of %r with value: %r",
                    self._config[CONF_VALUE_TEMPLATE],
                    value,
                )
                return
        self._state = value
