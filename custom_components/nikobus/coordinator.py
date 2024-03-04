"""Coordinator for Nikobus."""
from typing import Any
from datetime import timedelta

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Nikobus custom coordinator for integrating with the Home Assistant platform.
    
    This coordinator is responsible for managing the communication between Home Assistant
    and the Nikobus system. It fetches the latest state from Nikobus and updates Home Assistant entities.
    """

    def __init__(self, hass: HomeAssistant, api) -> None:
        """Initialize the coordinator.

        Parameters:
        - hass: HomeAssistant object, provides access to Home Assistant core.
        - api: The API interface object for interacting with Nikobus.
        """
        self.api = api
        self.hass = hass

        async def async_update_data():
            """Fetch data from Nikobus.

            This method is called periodically and is responsible for fetching the latest
            data from Nikobus. If an error occurs during data fetching, it logs the error
            and raises an UpdateFailed exception to notify the update coordinator.
            """
            try:
                return await api.refresh_nikobus_data()
            except Exception as e:
                _LOGGER.error("Error fetching Nikobus data: %s", e)
                raise UpdateFailed(f"Error fetching data: {e}")

        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=async_update_data,
            update_interval=timedelta(seconds=120),  # Defines how often data should be updated.
        )

    async def update_json_state(self, address, channel, value):
        """Update the JSON state in the Nikobus system.

        This method updates the state of a device in the Nikobus system based on the address, channel, and new value.

        Parameters:
        - address: The address of the device to update.
        - channel: The channel of the device to update.
        - value: The new value to set for the device.
        """
        await self.api.update_json_state(address, channel, value)

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

    async def operate_cover(self, address, channel, direction):
        """Operate a cover to either open or close based on the direction.

        This method abstracts the control of covers by determining the operation needed
        (open or close) based on the 'direction' parameter provided.

        Parameters:
        - address: The address of the cover's controller.
        - channel: The channel of the cover to be controlled.
        - direction: The operation direction, either 'open' or 'close'.
        """
        if direction == 'open':
            await self.api.open_cover(address, channel)
        else:
            await self.api.close_cover(address, channel)

    async def open_cover(self, address, channel) -> None:
        """Open the cover.

        Parameters:
        - address: The address of the cover's controller.
        - channel: The channel of the cover.
        """
        await self.api.open_cover(address, channel)

    async def close_cover(self, address, channel) -> None:
        """Close the cover.

        Parameters:
        - address: The address of the cover's controller.
        - channel: The channel of the cover.
        """
        await self.api.close_cover(address, channel)

    async def stop_cover(self, address, channel) -> None:
        """Stop the cover.

        Parameters:
        - address: The address of the cover's controller.
        - channel: The channel of the cover.
        """
        await self.api.stop_cover(address, channel)

    async def send_button_press(self, address) -> None:
        """Send a button press command to Nikobus.

        This method is used to simulate a button press in the Nikobus system. It can be used for various control actions.

        Parameters:
        - address: The address of the button to be pressed.
        """
        await self.api.send_button_press(address)
