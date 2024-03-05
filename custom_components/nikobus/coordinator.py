"""Coordinator for Nikobus."""
from typing import Any
from datetime import timedelta

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, api) -> None:
        self.api = api
        self.hass = hass

        async def async_update_data():
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
        await self.api.update_json_state(address, channel, value)

#### SWITCH 
    def get_switch_state(self, address, channel):
        return self.api.get_switch_state(address, channel)

    async def turn_on_switch(self, address, channel) -> None:
        await self.api.turn_on_switch(address, channel)

    async def turn_off_switch(self, address, channel) -> None:
        await self.api.turn_off_switch(address, channel)

#### LIGHT
    def get_light_state(self, address, channel):
        return self.api.get_light_state(address, channel)

    def get_light_brightness(self, address, channel):
        return self.api.get_light_brightness(address, channel)

    async def turn_on_light(self, address, channel, brightness) -> None:
        await self.api.turn_on_light(address, channel, brightness)

    async def turn_off_light(self, address, channel) -> None:
        await self.api.turn_off_light(address, channel)

#### COVER 
    def get_cover_state(self, address, channel):
        return self.api.get_cover_state(address, channel)

    async def operate_cover(self, address, channel, direction):
        if direction == 'open':
            await self.api.open_cover(address, channel)
        else:
            await self.api.close_cover(address, channel)

    async def open_cover(self, address, channel) -> None:
        await self.api.open_cover(address, channel)

    async def close_cover(self, address, channel) -> None:
        await self.api.close_cover(address, channel)

    async def stop_cover(self, address, channel) -> None:
        await self.api.stop_cover(address, channel)

#### BUTTON
    async def send_button_press(self, address) -> None:
        await self.api.send_button_press(address)
