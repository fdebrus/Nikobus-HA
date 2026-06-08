"""Nikobus Configuration Handler - Load configuration files for Nikobus.

As of nikobus-connect 0.4.0 the module store lives in the HA Store
(``.storage/nikobus.modules``). This handler only loads the scene config
file today; the module/button paths are intentionally absent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiofiles import open as aio_open
from homeassistant.core import HomeAssistant

from .exceptions import NikobusDataError

_LOGGER = logging.getLogger(__name__)


class NikobusConfig:
    """Handles the loading and saving of Nikobus configuration data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the configuration handler."""
        self._hass = hass

    async def load_json_data(self, file_name: str, data_type: str) -> dict[str, Any]:
        """Load JSON data from a config file in the HA config directory."""
        file_path = self._hass.config.path(file_name)
        _LOGGER.info("Loading %s data from %s", data_type, file_path)

        try:
            async with aio_open(file_path, mode="r") as file:
                return json.loads(await file.read())

        except FileNotFoundError:
            self._handle_file_not_found(file_path, data_type)
            await self._create_empty_config(file_path, data_type)
            return {}

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to decode JSON in %s file: %s", data_type, err, exc_info=True)
            raise NikobusDataError(f"Failed to decode JSON in {data_type} file: {err}") from err

        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error("Failed to load %s data: %s", data_type, err, exc_info=True)
            raise NikobusDataError(f"Failed to load {data_type} data: {err}") from err

    def _handle_file_not_found(self, file_path: str, data_type: str) -> None:
        """Handle the case where the configuration file is not found."""
        _LOGGER.info(
            "%s configuration file not found: %s. Skipping.",
            data_type.capitalize(),
            file_path,
        )

    @staticmethod
    async def _create_empty_config(file_path: str, data_type: str) -> None:
        """Create an empty skeleton config file so the library can update it later."""
        _EMPTY_SKELETONS: dict[str, dict[str, Any]] = {
            "scene": {},
        }
        skeleton = _EMPTY_SKELETONS.get(data_type, {})
        try:
            async with aio_open(file_path, "w") as file:
                await file.write(json.dumps(skeleton, indent=4))
            _LOGGER.info("Created empty %s config: %s", data_type, file_path)
        except OSError as err:
            _LOGGER.warning("Could not create empty %s config: %s", data_type, err)
