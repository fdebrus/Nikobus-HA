"""Coordinator for Nikobus."""
from typing import Any
from datetime import timedelta

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Nikobus custom coordinator."""

    def __init__(self, hass: HomeAssistant, api) -> None:
        """
        Initialize the coordinator.

        Parameters:
        - hass: The Home Assistant instance.
        - api: The API used for communication with Nikobus devices.
        """
        # Call the __init__ method of the superclass with necessary parameters
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=api.refresh_nikobus_data,
            update_interval=timedelta(seconds=120)
        )
        # Store the API instance for later use
        self.api = api
        self.hass = hass

#### GENERAL
    async def get_output_state(self, address, channel) -> Any:
        """
        Get the state of an output.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the output.

        Returns:
        - The state of the output.
        """
        _state = self.api.get_output_state(address, channel)
        
        _LOGGER.debug("get_output_state:%s %s %s",address, channel, _state)
        return _state
####

#### SWITCHES
    def get_switch_state(self, address, channel):
        """
        Get the state of a switch.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the switch.

        Returns:
        - The state of the switch.
        """
        return self.api.get_switch_state(address, channel)

    async def turn_on_switch(self, address, channel) -> None:
        """
        Turn on a switch.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the switch.
        """
        await self.api.turn_on_switch(address, channel)

    async def turn_off_switch(self, address, channel) -> None:
        """
        Turn off a switch.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the switch.
        """
        await self.api.turn_off_switch(address, channel)
####

#### DIMMERS
    def get_light_state(self, address, channel):
        """
        Get the state of a light.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the light.

        Returns:
        - The state of the light.
        """
        return self.api.get_light_state(address, channel)
        
    def get_light_brightness(self, address, channel):
        """
        Get the brightness of a light.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the light.

        Returns:
        - The brightness of the light.
        """
        return self.api.get_light_brightness(address, channel)

    async def turn_on_light(self, address, channel, brightness) -> None:
        """
        Turn on a light with specified brightness.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the light.
        - brightness: The brightness to set the light to.
        """
        await self.api.turn_on_light(address, channel, brightness)

    async def turn_off_light(self, address, channel) -> None:
        """
        Turn off a light.

        Parameters:
        - address: The address of the controller.
        - channel: The channel of the light.
        """
        await self.api.turn_off_light(address, channel)
####

#### COVERS
    async def operate_cover(self, address, channel, direction):
        if direction == 'open':
            await self.api.open_cover(address, channel)
        else:
            await self.api.close_cover(address, channel)

    async def open_cover(self, address, channel) -> None:
        """Open the cover."""
        await self.api.open_cover(address, channel)

    async def close_cover(self, address, channel) -> None:
        """Close the cover."""
        await self.api.close_cover(address, channel)

    async def stop_cover(self, address, channel) -> None:
        """Stop the cover."""
        await self.api.stop_cover(address, channel)
#### 

#### BUTTONS
    async def send_button_press(self, address) -> None:
        await self.api.send_button_press(address)
#### 
