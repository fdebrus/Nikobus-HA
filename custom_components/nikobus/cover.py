"""Nikobus Cover entity."""

import logging
import json
import asyncio
from datetime import datetime

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    ATTR_POSITION,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus cover entities for a Home Assistant config entry.
    This method retrieves data for each cover module configured within the Nikobus system
    and creates corresponding entities in Home Assistant.
    """
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

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

    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Represents a cover entity in the Nikobus system within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time) -> None:
        """Initialize the Nikobus cover entity with the specified parameters."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._position = 100  # Assume the cover is fully open initially
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._nikobus_command = False
        self._operation_time = float(operation_time)  # Convert operation time to float
        self._name = channel_description
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._unique_id = f"{self._address}{self._channel}"

    @property
    def device_info(self):
        """Return device information for Home Assistant."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def name(self):
        """Return the name of the cover entity."""
        return self._name

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self._position

    @property
    def is_closed(self):
        """Check if the cover is fully closed."""
        return self._position == 0

    @property
    def supported_features(self):
        """Indicate which features are supported by the cover entity."""
        return (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )

    @property
    def is_opening(self):
        """Check if the cover is currently opening."""
        return self._is_opening

    @property
    def is_closing(self):
        """Check if the cover is currently closing."""
        return self._is_closing

    @property
    def unique_id(self):
        """Return a unique identifier for this entity."""
        return self._unique_id

    async def async_added_to_hass(self):
        """Handle additional setup when the entity is added to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"nikobus_cover_update_{self._unique_id}",
                self._handle_signal
            )
        )

    async def _handle_signal(self, message):
        """Handle incoming commands to open or close the cover."""
        # If a command is received to open/close the cover, and the cover is already moving, stop it first.
        if message['command'] == 'close':
            self._nikobus_command = True
            if self._is_opening or self._is_closing:
                await self.async_stop_cover()
            else:            
                await self.async_close_cover()
        if message['command'] == 'open':
            self._nikobus_command = True
            if self._is_opening or self._is_closing:
                await self.async_stop_cover()
            else:
                await self.async_open_cover()

    async def async_open_cover(self, **kwargs):
        """Open the cover. Log an error if the operation fails."""
        _LOGGER.debug("OPEN COVER")
        if self._position < 100:  # Check if the cover is not fully open
            self._is_opening = True
            self._is_closing = False
            try:
                if not self._nikobus_command:
                    # Only send the command if it wasn't triggered by a Nikobus command
                    await self._dataservice.operate_cover(self._address, self._channel, "open")
                self._nikobus_command = False
                await self._complete_movement(100)
            except Exception as e:
                _LOGGER.error(f"Error during cover operation: {e}")

    async def async_close_cover(self, **kwargs):
        """Close the cover. Log an error if the operation fails."""
        _LOGGER.debug("CLOSE COVER")
        if self._position > 0:  # Check if the cover is not fully closed
            self._is_closing = True
            self._is_opening = False
            try:
                if not self._nikobus_command:
                    # Only send the command if it wasn't triggered by a Nikobus command
                    await self._dataservice.operate_cover(self._address, self._channel, "close")
                self._nikobus_command = False
                await self._complete_movement(0)
            except Exception as e:
                _LOGGER.error(f"Error during cover operation: {e}")

    async def async_stop_cover(self):
        """Stop any ongoing cover movement."""
        _LOGGER.debug("STOP cover")
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self.async_write_ha_state()
        await self._dataservice.stop_cover(self._address, self._channel)

    async def async_set_cover_position(self, **kwargs):
        """Set the cover to a specific position. Calculate the direction and duration needed to reach the desired position."""
        _LOGGER.debug("SET POS COVER")
        expected_position = int(kwargs.get(ATTR_POSITION))
        direction = "open" if expected_position > self._position else "close"
        if direction == "open":
            self._is_opening = True
            self._is_closing = False
        else:
            self._is_closing = True
            self._is_opening = False
        if not self._nikobus_command:
            await self._dataservice.operate_cover(self._address, self._channel, direction)
        self._nikobus_command = False
        await self._complete_movement(expected_position)
        
    async def _complete_movement(self, expected_position):
        """Complete the cover's movement to the expected position."""
        _LOGGER.debug("MOVEMENT COVER")
        position_diff = abs(self._position - expected_position)
        proportional_time_needed = (position_diff / 100.0) * self._operation_time
        await self._update_position_in_real_time(expected_position, proportional_time_needed)

    async def _update_position_in_real_time(self, expected_position, total_time):
        """Update the cover's position in real time until the movement is complete."""
        _LOGGER.debug("UPDATE COVER")
        start_time = datetime.now()
        initial_position = self._position
        direction = 1 if expected_position > initial_position else -1
        while self._in_motion: 
            elapsed_time = (datetime.now() - start_time).total_seconds()
            if elapsed_time >= total_time:
                self._position = expected_position
                self._in_motion = False
                await self.async_stop_cover()
                break
            progress = elapsed_time / total_time
            self._position = initial_position + (direction * int(progress * (expected_position - initial_position)))
            self._position = max(0, min(100, self._position))
            self.async_write_ha_state()
            await asyncio.sleep(1)
        await self._dataservice.update_json_state(self._address, self._channel, '00')
        self._is_opening = False
        self._is_closing = False
        self.async_write_ha_state()
