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
                    data['nikobus_button'] = {button['address']: button for button in data['nikobus_button']}
                else:
                    _LOGGER.warning(f"'nikobus_button' key not found in {data_type} data")
            elif data_type == "module":
                if 'switch_module' in data:
                    data['switch_module'] = {module['address']: module for module in data['switch_module']}
                if 'dimmer_module' in data:
                    data['dimmer_module'] = {module['address']: module for module in data['dimmer_module']}
                if 'roller_module' in data:
                    data['roller_module'] = {module['address']: module for module in data['roller_module']}
            return data
        except FileNotFoundError:
            _LOGGER.error(f'{data_type.capitalize()} file not found: {file_path}')
        except json.JSONDecodeError as e:
            _LOGGER.error(f'Failed to decode JSON in {data_type} file: {e}')
        except Exception as e:
            _LOGGER.error(f'Failed to load {data_type} data: {e}')
        return None

    async def write_json_data(self, file_name: str, data_type: str, data: dict) -> None:
        """Write button data to a JSON file, transforming it into a list format."""
        button_config_file_path = self._hass.config.path(file_name)
    
        try:

            nikobus_button_data = data.get("nikobus_button", {})
            data_list = []
            for address, details in nikobus_button_data.items():
            
                button_data = {
                    "description": details["description"],
                    "address": address,
                    "impacted_module": details["impacted_module"]
                }
                data_list.append(button_data)
        
            final_data = {"nikobus_button": data_list}
        
            async with aio_open(button_config_file_path, 'w') as file:
                json_data = json.dumps(final_data, indent=4)
                await file.write(json_data)

        except IOError as e:
            _LOGGER.error(f"Failed to write {data_type} data to file {file_name}: {e}")
        except TypeError as e:
            _LOGGER.error(f"Failed to serialize {data_type} data to JSON: {e}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error writing {data_type} data to file {file_name}: {e}")