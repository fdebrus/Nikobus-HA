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
        self._position = 100  # Assume the cover is fully open initially.
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._nikobus_command = False
        self._operation_time = float(operation_time)  # Operation time in seconds to fully open/close the cover.
        self._name = channel_description
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._unique_id = f"{self._address}{self._channel}"

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
    def name(self):
        """Returns the name of the cover entity."""
        return self._name

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
    def unique_id(self):
        """Provides a unique identifier for the cover entity."""
        return self._unique_id

    async def async_added_to_hass(self):
        """Actions to perform when the entity is added to Home Assistant, such as registering update listeners."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"nikobus_cover_update_{self._unique_id}",
                self._handle_signal
            )
        )

    async def _handle_signal(self, message):
        """Handles update signals for the cover, directing the entity to open or close based on received commands."""
        # Process received commands to open or close the cover.
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
        """Triggers the operation to fully open the cover."""
        _LOGGER.debug(f"Initiating opening of cover at {self._address}.")
        if self._position < 100:  # Verify the cover is not already fully open.
            self._is_opening = True
            self._is_closing = False
            if not self._nikobus_command:
                await self._dataservice.operate_cover(self._address, self._channel, "open")
            self._nikobus_command = False
            await self._complete_movement(100)

    async def async_close_cover(self, **kwargs):
        """Triggers the operation to fully close the cover."""
        _LOGGER.debug(f"Initiating closing of cover at {self._address}.")
        if self._position > 0:  # Verify the cover is not already fully closed.
            self._is_closing = True
            self._is_opening = False
            if not self._nikobus_command:
                await self._dataservice.operate_cover(self._address, self._channel, "close")
            self._nikobus_command = False
            await self._complete_movement(0)

    async def async_stop_cover(self):
        """Stops any ongoing movement of the cover."""
        _LOGGER.debug(f"Stopping movement of cover at {self._address}.")
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self.async_write_ha_state()
        await self._dataservice.stop_cover(self._address, self._channel)

    async def async_set_cover_position(self, **kwargs):
        """Sets the cover to a specific position."""
        _LOGGER.debug(f"Setting position of cover at {self._address}.")
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
        """Completes the movement of the cover to the expected position."""
        _LOGGER.debug(f"Completing movement to {expected_position}% for cover at {self._address}.")
        position_diff = abs(self._position - expected_position)
        proportional_time_needed = (position_diff / 100.0) * self._operation_time
        await self._update_position_in_real_time(expected_position, proportional_time_needed)

    async def _update_position_in_real_time(self, expected_position, total_time):
        """Updates the cover's position in real time until the movement is completed."""
        _LOGGER.debug(f"Updating position for cover at {self._address} in real time.")
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
                await self.async_stop_cover()
                break

            progress = elapsed_time / total_time
            self._position = initial_position + int(progress * position_change) if direction == 1 else initial_position - int(progress * position_change)
            self._position = max(0, min(100, self._position))

            self.async_write_ha_state()
            await asyncio.sleep(1)

        await self._dataservice.update_json_state(self._address, self._channel, '00')
        self._is_opening = False
        self._is_closing = False
        self.async_write_ha_state()
