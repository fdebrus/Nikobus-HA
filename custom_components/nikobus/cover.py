import logging
import json
import asyncio
from datetime import datetime

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    ATTR_POSITION
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    # Iteration over shutter modules and their channels
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
        for i, channel in enumerate(cover_module["channels"])
    ]

    # Add created entities to Home Assistant
    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Nikobus Cover Entity."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time) -> None:
        """Initialize a Nikobus Cover Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._position = 100 
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._nikobus_command = False
        self._operation_time = float(operation_time) # Time in seconds to fully open/close
        self._name = channel_description
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._unique_id = f"{self._address}{self._channel}"

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def name(self):
        """Return the name of the cover."""
        return self._name

    @property
    def current_cover_position(self):
        return self._position

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self._position == 0

    @property
    def supported_features(self):
        """Flag supported features."""
        return (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        return self._is_opening

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        return self._is_closing

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id

    async def async_added_to_hass(self):
        """Entity is added to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"nikobus_cover_update_{self._unique_id}",
                self._handle_signal
            )
        )

    async def _handle_signal(self, message):
        """Handle incoming signal."""
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
        """Open the cover."""
        _LOGGER.debug("OPEN COVER")
        if self._position < 100:  # Ensure there's a need to open the cover
            self._is_opening = True
            self._is_closing = False
            try:
                if not self._nikobus_command:
                    await self._dataservice.operate_cover(self._address, self._channel, "open")
                self._nikobus_command = False
                await self._complete_movement(100)
            except Exception as e:
                _LOGGER.error(f"Error during cover operation: {e}")

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        _LOGGER.debug("CLOSE COVER")
        if self._position > 0:  # Ensure there's a need to close the cover
            self._is_closing = True
            self._is_opening = False
            try:
                if not self._nikobus_command:
                    await self._dataservice.operate_cover(self._address, self._channel, "close")
                self._nikobus_command = False
                await self._complete_movement(0)
            except Exception as e:
                _LOGGER.error(f"Error during cover operation: {e}")

    async def async_stop_cover(self):
        """Stop the cover movement."""
        _LOGGER.debug("STOP cover")
        # Reset operation flags
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self.async_write_ha_state()
        # Issue the stop command to the cover device
        await self._dataservice.stop_cover(self._address, self._channel)

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        _LOGGER.debug("SET POS COVER")
        expected_position = int(kwargs.get(ATTR_POSITION))

        # Determine if we need to open or close based on the current and expected positions
        direction = "open" if expected_position > self._position else "close"

        # Start moving in the determined direction
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
        """Handle completion of movement towards the desired position."""
        _LOGGER.debug("MOVEMENT COVER")
        position_diff = abs(self._position - expected_position)
        proportional_time_needed = (position_diff / 100.0) * self._operation_time
        await self._update_position_in_real_time(expected_position, proportional_time_needed)

    async def _update_position_in_real_time(self, expected_position, total_time):
        _LOGGER.debug("UPDATE COVER")
        start_time = datetime.now()
        initial_position = self._position
        direction = 1 if expected_position > initial_position else -1
        position_change = abs(expected_position - initial_position)

        self._in_motion = True
        while self._in_motion: 
            elapsed_time = (datetime.now() - start_time).total_seconds()
            if elapsed_time >= total_time:
                # If the operation is completed or exceeded the expected time,
                # set the position directly to the expected position.
                self._position = expected_position
                self._in_motion = False
                await self.async_stop_cover()
                break

            # Calculate the progress as a fraction of the total time
            progress = elapsed_time / total_time

            # Calculate the new position based on the direction and progress
            if direction == 1:  # Opening
                self._position = initial_position + int(progress * position_change)
            else:  # Closing
                self._position = initial_position - int(progress * position_change)

            # Ensure the position does not exceed bounds
            self._position = max(0, min(100, self._position))

            self.async_write_ha_state()  # Update the state in Home Assistant
            await asyncio.sleep(1)  # Throttle updates to avoid flooding Home Assistant with too many state changes

        self._is_opening = False
        self._is_closing = False
        self.async_write_ha_state()
