"""Nikobus component."""
from __future__ import annotations

import logging
import select
import socket
from typing import Any, Final

import voluptuous as vol

from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_NAME,
    CONF_TIMEOUT,
    CONF_BUFFER_SIZE,
)

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType

from .const import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_TIMEOUT,
    DEFAULT_NAME,
)

_LOGGER: Final = logging.getLogger(__name__)

"""
TCP_PLATFORM_SCHEMA: Final[dict[vol.Marker, Any]] = {
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PORT): cv.port,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Optional(CONF_BUFFER_SIZE, default=DEFAULT_BUFFER_SIZE): cv.positive_int,
}
"""

class TcpEntity(Entity):
    """Base entity class for TCP platform."""

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        value_template.hass = hass
        self._hass = hass
        self._state: str | None = None
        self.update()

    @property
    def name(self) -> str:
        """Return the name of this sensor."""
        return self._config[CONF_NAME]

    def update(self, host, port) -> None:
        """Get the latest value for this sensor."""
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

            try:
                sock.send(self._config[CONF_PAYLOAD].encode())
            except OSError as err:
                _LOGGER.error(
                    "Unable to send payload %r to %s on port %s: %s",
                    self._config[CONF_PAYLOAD],
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
                    host,
                    port,
                )
                return

            value = sock.recv(self._config[CONF_BUFFER_SIZE]).decode()
            
        if value is not None:
            try:
                self._state = value
                return
            except TemplateError:
                _LOGGER.error(
                    "Unable to read value: %r",
                    value,
                )
                return

        self._state = value
