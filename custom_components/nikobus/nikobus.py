"""Nikobus API"""
from __future__ import annotations

import logging
import select
import socket
import ssl
from typing import Any, Final

import voluptuous as vol

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PAYLOAD,
    CONF_PORT,
    CONF_SSL,
    CONF_TIMEOUT,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_VALUE_TEMPLATE,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BUFFER_SIZE,
    CONF_VALUE_ON,
    DEFAULT_BUFFER_SIZE,
    DEFAULT_NAME,
    DEFAULT_SSL,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
)
from .model import TcpSensorConfig

TCP_PLATFORM_SCHEMA: Final[dict[vol.Marker, Any]] = {
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PORT): cv.port,
    vol.Required(CONF_PAYLOAD): cv.string,
    vol.Optional(CONF_BUFFER_SIZE, default=DEFAULT_BUFFER_SIZE): cv.positive_int,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
    vol.Optional(CONF_VALUE_ON): cv.string,
    vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
    vol.Optional(CONF_SSL, default=DEFAULT_SSL): cv.boolean,
    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
}

__title__ = "Nikobus"
__version__ = "0.0.1"
__author__ = "Frederic Debrus"
__license__ = "MIT"

_LOGGER = logging.getLogger(__name__)

class NikobusBridge(entity):
    
    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        """Initialize Nikobus Connection"""
        self._hass = hass
        self._config: TcpSensorConfig = {
            CONF_NAME: config[CONF_NAME],
            CONF_HOST: config[CONF_HOST],
            CONF_PORT: config[CONF_PORT],
            CONF_TIMEOUT: config[CONF_TIMEOUT],
            CONF_PAYLOAD: config[CONF_PAYLOAD],
            CONF_UNIT_OF_MEASUREMENT: config.get(CONF_UNIT_OF_MEASUREMENT),
            CONF_VALUE_TEMPLATE: value_template,
            CONF_VALUE_ON: config.get(CONF_VALUE_ON),
            CONF_BUFFER_SIZE: config[CONF_BUFFER_SIZE],
            CONF_SSL: config[CONF_SSL],
            CONF_VERIFY_SSL: config[CONF_VERIFY_SSL],
        }
        self.update()

    def update(self) -> None:
        """Get the latest value."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._config[CONF_TIMEOUT])
            try:
                sock.connect((self._config[CONF_HOST], self._config[CONF_PORT]))
            except OSError as err:
                _LOGGER.error(
                    "Unable to connect to %s on port %s: %s",
                    self._config[CONF_HOST],
                    self._config[CONF_PORT],
                    err,
                )
                return

            try:
                sock.send(self._config[CONF_PAYLOAD].encode())
            except OSError as err:
                _LOGGER.error(
                    "Unable to send payload %r to %s on port %s: %s",
                    self._config[CONF_PAYLOAD],
                    self._config[CONF_HOST],
                    self._config[CONF_PORT],
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
