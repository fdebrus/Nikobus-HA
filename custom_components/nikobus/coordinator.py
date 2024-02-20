"""Coordinator for Nikobus."""
import os
import json
import textwrap

import logging
from typing import Any


from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Nikobus custom coordinator."""

    def __init__(self, hass: HomeAssistant, api) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
        )
        self.api = api
        self.json_config_data = None
        self.json_status_data = None

    # async def async_updated_data(self, data) -> None:
    #    """Update data."""
    #    super().async_set_updated_data(data)

    async def initial_data_load(self):
        # Open the JSON file and load its contents
        current_file_path = os.path.abspath(__file__)
        current_directory = os.path.dirname(current_file_path)
        config_file_path = os.path.join(current_directory, "nikobus_config.json")
        with open(config_file_path, 'r') as file:
            self.json_config_data = json.load(file)
        await self.refresh_data()

    async def refresh_data(self):
        result_dict = {} 
        state_group1 = ""
        state_group2 = ""
        for module_type in ['dimmer_modules_addresses', 'switch_modules_addresses']:
            for entry in self.json_config_data[module_type]:
                actual_address = entry.get("address")
                state_group1 = await self.api.get_output_state(address=actual_address, group=1, timeout=3)
                state_group2 = await self.api.get_output_state(address=actual_address, group=2, timeout=3)

                if state_group2 is not None:
                    state_groups = state_group1 + state_group2
                else:
                    state_groups = state_group1
                    
                state_group_array = {str(index): item for index, item in enumerate(textwrap.wrap(state_groups, width=2))}
                result_dict[actual_address] = state_group_array

        json_status_data = json.dumps(result_dict)
        _LOGGER.debug("json: %s",json_status_data)

    def get_switch_status(self, address, channel):
        _status = self.json_status_data.get(address, {}).get(channel)
        if _status == "FF":
            return True
        else:
            return False

    def get_output_state(self, address, channel, timeout) -> Any:
        """Return status of address channel."""
        return self.api.get_output_state(address, channel, timeout)

    async def turn_on_switch(self, address, channel) -> None:
        """Turn on address channel."""
        await self.api.turn_on_switch(address, channel)

    async def turn_off_switch(self, address, channel) -> None:
        """Turn off address channel."""
        await self.api.turn_off_switch(address, channel)

    async def turn_on_light(self, address, channel) -> None:
        """Turn on address channel."""
        await self.api.turn_on_light(address, channel)

    async def turn_off_light(self, address, channel) -> None:
        """Turn off address channel."""
        await self.api.turn_off_light(address, channel)

    async def open_cover(self, address, channel) -> None:
        """Open the cover."""
        await self.api.open_cover(address, channel)

    async def async_close_cover(self, address, channel) -> None:
        """Close the cover."""
        await self.api.close_cover(address, channel)

    async def async_stop_cover(self, address, channel) -> None:
        """Stop the cover."""
        await self.api.stop_cover(address, channel)

    async def get_cover_state(self, address, channel) -> None:
        """Update the state of the cover."""
        await self.api.get_cover_state(address, channel)