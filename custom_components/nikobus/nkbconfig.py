"""Load / Write configuration files for Nikobus"""

import json
from aiofiles import open as aio_open
import logging
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)
__version__ = '0.1'

class NikobusConfig:
    """Handles the loading and saving of Nikobus configuration data."""

    def __init__(self, hass):
        """Initialize the configuration handler."""
        self._hass = hass

    async def load_json_data(self, file_name: str, data_type: str) -> dict | None:
        """Load JSON data from a file and transform it based on the data type."""
        file_path = self._hass.config.path(file_name)
        _LOGGER.info(f'Loading {data_type} data from {file_path}')
        try:
            async with aio_open(file_path, mode='r') as file:
                data = json.loads(await file.read())

            return self._transform_loaded_data(data, data_type)

        except FileNotFoundError:
            self._handle_file_not_found(file_path, data_type)
        except json.JSONDecodeError as e:
            _LOGGER.error(f'Failed to decode JSON in {data_type} file: {e}')
            raise HomeAssistantError(f'Failed to decode JSON in {data_type} file: {e}') from e
        except Exception as e:
            _LOGGER.error(f'Failed to load {data_type} data: {e}')
            raise HomeAssistantError(f'Failed to load {data_type} data: {e}') from e
        return None

    def _transform_loaded_data(self, data: dict, data_type: str) -> dict:
        """Transform the loaded JSON data based on the data type."""
        if data_type == "button":
            return self._transform_button_data(data)
        elif data_type == "module":
            return self._transform_module_data(data)
        return data

    def _transform_button_data(self, data: dict) -> dict:
        """Transform button data from a list to a dictionary."""
        if 'nikobus_button' in data:
            data['nikobus_button'] = {button['address']: button for button in data['nikobus_button']}
        else:
            _LOGGER.warning(f"'nikobus_button' key not found in button data")
        return data

    def _transform_module_data(self, data: dict) -> dict:
        """Transform module data from a list to a dictionary."""
        for key in ['switch_module', 'dimmer_module', 'roller_module']:
            if key in data:
                data[key] = {module['address']: module for module in data[key]}
        return data

    def _handle_file_not_found(self, file_path: str, data_type: str) -> None:
        """Handle the case where the configuration file is not found."""
        if data_type == "button":
            _LOGGER.info(f'Button configuration file not found: {file_path}. A new file will be created upon discovering the first button.')
        else:
            raise HomeAssistantError(f'{data_type.capitalize()} configuration file not found: {file_path}')

    async def write_json_data(self, file_name: str, data_type: str, data: dict) -> None:
        """Write data to a JSON file, transforming it into a list format if necessary."""
        file_path = self._hass.config.path(file_name)
        try:
            transformed_data = self._transform_data_for_writing(data_type, data)
            async with aio_open(file_path, 'w') as file:
                json_data = json.dumps(transformed_data, indent=4)
                await file.write(json_data)

        except IOError as e:
            _LOGGER.error(f'Failed to write {data_type.capitalize()} data to file {file_name}: {e}')
            raise HomeAssistantError(f'Failed to write {data_type.capitalize()} data to file {file_name}: {e}') from e
        except TypeError as e:
            _LOGGER.error(f'Failed to serialize {data_type} data to JSON: {e}')
            raise HomeAssistantError(f'Failed to serialize {data_type} data to JSON: {e}') from e
        except Exception as e:
            _LOGGER.error(f'Unexpected error writing {data_type} data to file {file_name}: {e}')
            raise HomeAssistantError(f'Unexpected error writing {data_type} data to file {file_name}: {e}') from e

    def _transform_data_for_writing(self, data_type: str, data: dict) -> dict:
        """Transform the data for writing based on the data type."""
        if data_type == "button":
            return self._transform_button_data_for_writing(data)
        # Add other data types here if needed
        return data

    def _transform_button_data_for_writing(self, data: dict) -> dict:
        """Transform button data from a dictionary back to a list for saving."""
        button_data_list = []
        for address, details in data.get("nikobus_button", {}).items():
            button_data = {
                "description": details["description"],
                "address": address,
                "impacted_module": details["impacted_module"]
            }
            button_data_list.append(button_data)
        return {"nikobus_button": button_data_list}
