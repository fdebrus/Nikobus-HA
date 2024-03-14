"""Nikobus Cover entity."""

import json
import asyncio
from datetime import datetime

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    ATTR_POSITION
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    """Set up Nikobus cover entities from a configuration entry. This function initializes cover entities based on the Nikobus system's configuration and adds them to Home Assistant for management."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    # Create cover entities for each configured roller module and its channels not marked as "not_in_use".
    entities = [
        NikobusCoverEntity(
            hass,
            dataservice,
            cover_module.get("description"),
            cover_module.get("model"),
            cover_module.get("address"),
            i,
            channel["description"],
            channel["operation_time"],
        )
        for cover_module in dataservice.api.json_config_data["roller_modules_addresses"]
        for i, channel in enumerate(cover_module["channels"], start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    # Add the cover entities to Home Assistant.
    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Represents a Nikobus cover entity within Home Assistant, providing control over a physical cover in the Nikobus system."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time) -> None:
        """Initialize the cover entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._position = 100
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._nikobus_command = False
        self._operation_time = float(operation_time)  # Operation time in seconds to fully open/close the cover.
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"

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
    def current_cover_position(self):
        """Reports the current position of the cover."""
        return self._position

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

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.coordinator.async_add_listener(self._handle_coordinator_update)
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        state = self._dataservice.api.get_cover_state(self._address, self._channel)
        self._nikobus_command = True
        if state == 0x00:
            self._in_motion = False
        elif state == 0x01:
            if self._in_motion:
                self._in_motion = False
            else:
                self._in_motion = True
                self.hass.async_add_job(self._complete_movement(100))
        elif state == 0x02:
            if self._in_motion:
                self._in_motion = False
            else:
                self._in_motion = True
                self.hass.async_add_job(self._complete_movement(0))
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs):
        """Triggers the operation to fully open the cover."""
        if self._position < 100:  # Verify the cover is not already fully open.
            self._is_opening = True
            self._is_closing = False
            await self._operate_cover(self._address, self._channel, "open")
            await self._complete_movement(100)

    async def async_close_cover(self, **kwargs):
        """Triggers the operation to fully close the cover."""
        if self._position > 0:  # Verify the cover is not already fully closed.
            self._is_closing = True
            self._is_opening = False
            await self._operate_cover(self._address, self._channel, "close")
            await self._complete_movement(0)

    async def async_stop_cover(self):
        """Stops any ongoing movement of the cover."""
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self.async_write_ha_state()
        await self._dataservice.api.stop_cover(self._address, self._channel)

    async def async_set_cover_position(self, **kwargs):
        """Sets the cover to a specific position."""
        expected_position = int(kwargs.get(ATTR_POSITION))
        direction = "open" if expected_position > self._position else "close"
        if direction == "open":
            self._is_opening = True
            self._is_closing = False
        else:
            self._is_closing = True
            self._is_opening = False
        if not self._in_motion:
            await self._operate_cover(self._address, self._channel, direction)
        await self._complete_movement(expected_position)
        
    async def _complete_movement(self, expected_position):
        """Completes the movement of the cover to the expected position."""
        position_diff = abs(self._position - expected_position)
        proportional_time_needed = (position_diff / 100.0) * self._operation_time
        await self._update_position_in_real_time(expected_position, proportional_time_needed)

    async def _update_position_in_real_time(self, expected_position, total_time):
        """Updates the cover's position in real time until the movement is completed."""
        start_time = datetime.now()
        initial_position = self._position
        direction = 1 if expected_position > initial_position else -1
        position_change = abs(expected_position - initial_position)

        self._in_motion = True
        while self._in_motion: 
            elapsed_time = (datetime.now() - start_time).total_seconds()
            if elapsed_time >= total_time:
                self._position = expected_position
                self._in_motion = False
                if not self._nikobus_command:
                    await self.async_stop_cover()
                break

            progress = elapsed_time / total_time
            self._position = initial_position + int(progress * position_change) if direction == 1 else initial_position - int(progress * position_change)
            self._position = max(0, min(100, self._position))

            self.async_write_ha_state()
            await asyncio.sleep(1)

        self._dataservice.api.set_bytearray_state(self._address, self._channel, 0x00)
        self._is_opening = False
        self._is_closing = False
        self._nikobus_command = False
        self.async_write_ha_state()

    async def _operate_cover(self, address, channel, direction):
        if direction == 'open':
            await self._dataservice.api.open_cover(address, channel)
        else:
            await self._dataservice.api.close_cover(address, channel)
