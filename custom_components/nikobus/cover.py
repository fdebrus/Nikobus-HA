"""Nikobus Cover entity"""

import logging

import asyncio
from datetime import datetime

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass,
    ATTR_POSITION,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = [
        NikobusCoverEntity(
            hass,
            dataservice,
            cover_module_data.get("description"),
            cover_module_data.get("model"),
            address,
            i,
            channel["description"],
            channel.get("operation_time", "00"),
        )
        for address, cover_module_data in dataservice.api.dict_module_data['roller_module'].items() 
        for i, channel in enumerate(cover_module_data["channels"], start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Represents a Nikobus cover entity within Home Assistant, providing control over a physical cover in the Nikobus system."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time) -> None:
        """Initialize the cover entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._position = 100 # Assume we start with open shutters
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._movement_task = None
        self._operation_time = float(operation_time)  # Operation time in seconds to fully open/close the cover.
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

    @property
    def device_info(self):
        """Provides device information for Home Assistant to categorize and display the entity."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def device_class(self):
        """Return the class of this device."""
        return self._attr_device_class

    @property
    def current_cover_position(self):
        """Reports the current position of the cover."""
        return self._position

    @property
    def is_open(self):
        """Indicates whether the cover is fully closed."""
        return self._position == 100

    @property
    def is_closed(self):
        """Indicates whether the cover is fully closed."""
        return self._position == 0

    @property
    def supported_features(self):
        """Identifies the features supported by this cover entity, such as open, close, stop, and set position."""
        return (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )

    @property
    def is_opening(self):
        """Checks if the cover is currently opening."""
        return self._is_opening

    @property
    def is_closing(self):
        """Checks if the cover is currently closing."""
        return self._is_closing

    @property
    def is_stopped(self):
        """Checks if the cover is currently stopped."""
        return not (self._is_opening or self._is_closing)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        current_state = self._dataservice.api.get_cover_state(self._address, self._channel)

        if self._movement_task is None or (self._movement_task is not None and self._movement_task.done()):
            self._in_motion = current_state != 0x00
            self._is_opening = current_state == 0x01
            self._is_closing = current_state == 0x02
        
            if current_state == 0x01 and self._position != 100:
                self.hass.async_create_task(self._complete_movement(100))
            elif current_state == 0x02 and self._position != 0:
                self.hass.async_create_task(self._complete_movement(0))

    async def async_open_cover(self, **kwargs):
        if self._in_motion: 
            await self.async_cancel_current_movement()
        """Triggers the operation to fully open the cover."""
        self._is_closing = False
        self._is_opening = True
        self._in_motion = False
        await self._operate_cover(self._address, self._channel, "open")
        self._movement_task = asyncio.create_task(self._complete_movement(100))

    async def async_close_cover(self, **kwargs):
        if self._in_motion:
            await self.async_cancel_current_movement()
        """Triggers the operation to fully close the cover."""
        self._is_closing = True
        self._is_opening = False
        self._in_motion = False
        await self._operate_cover(self._address, self._channel, "close")
        self._movement_task = asyncio.create_task(self._complete_movement(0))

    async def async_stop_cover(self):
        if self._in_motion:
            await self.async_cancel_current_movement()
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        await self._dataservice.api.stop_cover(self._address, self._channel)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Sets the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)
        if self._in_motion:
            await self.async_cancel_current_movement()
        direction = "open" if target_position > self._position else "close"
        self._in_motion = False
        if direction == "open":
            self._is_opening = True
            self._is_closing = False
        else:
            self._is_opening = False
            self._is_closing = True
        await self._operate_cover(self._address, self._channel, direction)
        self._movement_task = asyncio.create_task(self._complete_movement(target_position))

    async def _complete_movement(self, expected_position):
        """Completes the movement of the cover to the expected position."""
        position_diff = abs(self._position - expected_position)
        proportional_time_needed = (position_diff / 100.0) * self._operation_time
        if proportional_time_needed > 0:
            await self._update_position_in_real_time(expected_position, proportional_time_needed)

    async def _update_position_in_real_time(self, expected_position, total_time):
        """Updates the cover's position in real time until the movement is completed."""
        start_time = datetime.now()
        initial_position = self._position
        direction = 1 if expected_position > initial_position else -1
        position_change = abs(expected_position - initial_position)

        self._in_motion = True

        while self._in_motion: 
            try:
                elapsed_time = (datetime.now() - start_time).total_seconds()
                if (direction == 1 and self._position >= expected_position) or (direction == -1 and self._position <= expected_position):
                    self._position = expected_position
                    self._in_motion = False
                    await self.async_stop_cover()
                    break

                progress = elapsed_time / total_time
                self._position = initial_position + int(progress * position_change) if direction == 1 else initial_position - int(progress * position_change)
                self._position = max(0, min(100, self._position))

                await asyncio.sleep(1)
                self.async_write_ha_state()

            except asyncio.CancelledError:
                _LOGGER.error("Movement operation error.")
                break 

        await self._reset_movement_state()

    async def _reset_movement_state(self):
        """Resets the state after movement is complete or cancelled."""
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self.async_write_ha_state()

    async def async_cancel_current_movement(self):
        """Cancels the current movement task if it's running."""
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.error("Movement task error.")
            self._movement_task = None
            self._in_motion = False

    async def _operate_cover(self, address, channel, direction):
        if direction == 'open':
            await self._dataservice.api.open_cover(address, channel)
        else:
            await self._dataservice.api.close_cover(address, channel)
