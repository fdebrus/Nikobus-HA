"""Nikobus Configuration Handler - Load / Write configuration files for Nikobus."""

import json
import logging
from typing import Any, Callable, Dict, Optional

from aiofiles import open as aio_open

from .exceptions import NikobusDataError  # Updated import

_LOGGER = logging.getLogger(__name__)
_LOAD_TRANSFORMS: Dict[str, str] = {
    "button": "_transform_button_data",
    "module": "_transform_module_data",
}
_WRITE_TRANSFORMS: Dict[str, str] = {
    "button": "_transform_button_data_for_writing",
}


class NikobusConfig:
    """Handles the loading and saving of Nikobus configuration data."""

    def __init__(self, hass: Any) -> None:
        """Initialize the configuration handler."""
        self._hass = hass

    async def load_json_data(self, file_name: str, data_type: str) -> dict:
        """Load JSON data from a file and transform it based on the data type."""
        file_path = self._hass.config.path(file_name)
        _LOGGER.info("Loading %s data from %s", data_type, file_path)

        try:
            async with aio_open(file_path, mode="r") as file:
                data = json.loads(await file.read())
            return self._transform_loaded_data(data, data_type)

        except FileNotFoundError as err:
            self._handle_file_not_found(file_path, data_type)
            if data_type in ("scene", "button"):
                return {}
            raise NikobusDataError(f"Missing required {data_type} file: {file_path}") from err

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to decode JSON in %s file: %s", data_type, err, exc_info=True)
            raise NikobusDataError(f"Failed to decode JSON in {data_type} file: {err}") from err

        except Exception as err:
            _LOGGER.error("Failed to load %s data: %s", data_type, err, exc_info=True)
            raise NikobusDataError(f"Failed to load {data_type} data: {err}") from err

    async def load_optional_json_data(self, file_name: str, data_type: str) -> dict:
        """Load JSON data from a file, returning an empty dict if it does not exist."""
        file_path = self._hass.config.path(file_name)
        _LOGGER.debug("Loading optional %s data from %s", data_type, file_path)

        try:
            async with aio_open(file_path, mode="r") as file:
                data = json.loads(await file.read())
            return self._transform_loaded_data(data, data_type)
        except FileNotFoundError:
            _LOGGER.debug("Optional %s file not found: %s", data_type, file_path)
            return {}
        except json.JSONDecodeError as err:
            _LOGGER.error(
                "Failed to decode JSON in optional %s file: %s",
                data_type,
                err,
                exc_info=True,
            )
            raise NikobusDataError(
                f"Failed to decode JSON in optional {data_type} file: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error(
                "Failed to load optional %s data: %s", data_type, err, exc_info=True
            )
            raise NikobusDataError(
                f"Failed to load optional {data_type} data: {err}"
            ) from err

    def _transform_loaded_data(self, data: dict, data_type: str) -> dict:
        """Transform the loaded JSON data based on the data type."""
        transform_name = _LOAD_TRANSFORMS.get(data_type)
        if not transform_name:
            return data
        return getattr(self, transform_name)(data)

    def _transform_button_data(self, data: dict) -> dict:
        """Transform button data from a list to a dictionary."""
        if "nikobus_button" in data:
            data["nikobus_button"] = {
                button["address"]: button for button in data["nikobus_button"]
            }
        else:
            _LOGGER.warning("'nikobus_button' key not found in button data")
        return data

    def _transform_module_data(self, data: dict) -> dict:
        """Transform module data from a list to a dictionary."""
        for key in ["switch_module", "dimmer_module", "roller_module", "other_module"]:
            if key not in data:
                continue
            modules = data[key]
            if isinstance(modules, list):
                data[key] = {
                    module["address"]: self._normalize_module_channels(module)
                    for module in modules
                    if module.get("address")
                }
            elif isinstance(modules, dict):
                normalized: dict[str, dict[str, Any]] = {}
                for address, module in modules.items():
                    if not isinstance(module, dict):
                        _LOGGER.warning(
                            "Skipping module %s with invalid data type: %s",
                            address,
                            type(module),
                        )
                        continue
                    module_data = dict(module)
                    module_data.setdefault("address", address)
                    normalized[address] = self._normalize_module_channels(module_data)
                data[key] = normalized
            else:
                _LOGGER.warning(
                    "Unsupported module data type for %s: %s", key, type(modules)
                )
                data[key] = {}
        return data

    @staticmethod
    def _normalize_module_channels(module: dict[str, Any]) -> dict[str, Any]:
        """Normalize module channel definitions to a list of channel dicts."""
        channels = module.get("channels", [])
        if isinstance(channels, int):
            module["channels"] = [
                {"description": f"Channel {idx}"} for idx in range(1, channels + 1)
            ]
            return module
        if isinstance(channels, list):
            module["channels"] = [
                channel if isinstance(channel, dict) else {"description": str(channel)}
                for channel in channels
            ]
            return module
        module["channels"] = []
        return module

    def _handle_file_not_found(self, file_path: str, data_type: str) -> None:
        """Handle the case where the configuration file is not found."""
        if data_type == "button":
            _LOGGER.info(
                "Button configuration file not found: %s. A new file will be created upon discovering the first button.",
                file_path,
            )
        elif data_type == "scene":
            _LOGGER.info(
                "Scene configuration file not found: %s. Skipping.",
                file_path,
            )
        else:
            raise NikobusDataError(
                f"{data_type.capitalize()} configuration file not found: {file_path}"
            )

    async def write_json_data(self, file_name: str, data_type: str, data: dict) -> None:
        """Write data to a JSON file, transforming it into a list format if necessary."""
        file_path = self._hass.config.path(file_name)

        try:
            transformed_data = self._transform_data_for_writing(data_type, data)
            async with aio_open(file_path, "w") as file:
                json_data = json.dumps(transformed_data, indent=4)
                await file.write(json_data)

        except IOError as err:
            _LOGGER.error(
                "Failed to write %s data to file %s: %s",
                data_type.capitalize(),
                file_name,
                err,
                exc_info=True,
            )
            raise NikobusDataError(
                f"Failed to write {data_type.capitalize()} data to file {file_name}: {err}"
            ) from err

        except TypeError as err:
            _LOGGER.error(
                "Failed to serialize %s data to JSON: %s",
                data_type,
                err,
                exc_info=True,
            )
            raise NikobusDataError(
                f"Failed to serialize {data_type} data to JSON: {err}"
            ) from err

        except Exception as err:
            _LOGGER.error(
                "Unexpected error writing %s data to file %s: %s",
                data_type,
                file_name,
                err,
                exc_info=True,
            )
            raise NikobusDataError(
                f"Unexpected error writing {data_type} data to file {file_name}: {err}"
            ) from err

    def _transform_data_for_writing(self, data_type: str, data: dict) -> dict:
        """Transform the data for writing based on the data type."""
        transform_name = _WRITE_TRANSFORMS.get(data_type)
        if not transform_name:
            return data
        return getattr(self, transform_name)(data)

    def _transform_button_data_for_writing(self, data: dict) -> dict:
        """Transform button data from a dictionary back to a list for saving."""
        button_data_list = [
            {
                "description": details["description"],
                "address": address,
                "impacted_module": details["impacted_module"],
            }
            for address, details in data.get("nikobus_button", {}).items()
        ]
        return {"nikobus_button": button_data_list}
