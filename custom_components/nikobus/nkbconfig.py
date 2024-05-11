"""Nikobus Config"""

import json
from aiofiles import open as aio_open

import logging

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

class NikobusConfig:

    def __init__(self, hass):
        self._hass = hass

    async def load_json_data(self, file_name: str, data_type: str) -> dict | None:
        file_path = self._hass.config.path(file_name)
        _LOGGER.info(f'Loading {data_type} data from {file_path}')
        try:
            async with aio_open(file_path, mode='r') as file:
                data = json.loads(await file.read())
            # Transform list to dictionary
            if data_type == "button":
                if 'nikobus_button' in data:
                    # data['nikobus_button'] = {button['address']: button for button in data['nikobus_button']}
                    pass
                else:
                    _LOGGER.warning(f"'nikobus_button' key not found in {data_type} data")
            elif data_type == "module":
                if 'switch_module' in data:
                    data['switch_module'] = {module['address']: module for module in data['switch_module']}
                else:
                    _LOGGER.warning(f"'switch_module' key not found in {data_type} data")
                if 'dimmer_module' in data:
                    data['dimmer_module'] = {module['address']: module for module in data['dimmer_module']}
                else:
                    _LOGGER.warning(f"'dimmer_module' key not found in {data_type} data")
                if 'roller_module' in data:
                    data['roller_module'] = {module['address']: module for module in data['roller_module']}
                else:
                    _LOGGER.warning(f"'roller_module' key not found in {data_type} data")
            return data
        except FileNotFoundError:
            _LOGGER.error(f'{data_type.capitalize()} file not found: {file_path}')
        except json.JSONDecodeError as e:
            _LOGGER.error(f'Failed to decode JSON in {data_type} file: {e}')
        except Exception as e:
            _LOGGER.error(f'Failed to load {data_type} data: {e}')
        return None

    async def write_json_data(self, file_name: str, data_type: str, data: dict) -> None:
        """Write the discovered button to a JSON file"""
        button_config_file_path = self._hass.config.path(file_name)
        try:
            async with aio_open(button_config_file_path, 'w') as file:
                json_data = json.dumps(data, indent=4)
                await file.write(json_data)
            _LOGGER.debug(f"{data_type.capitalize()} data successfully written to JSON file: {file_name}")
        except IOError as e:
            _LOGGER.error(f"Failed to write {data_type} data to file {file_name}: {e}")
        except TypeError as e:
            _LOGGER.error(f"Failed to serialize {data_type} data to JSON: {e}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error writing {data_type} data to file {file_name}: {e}")
