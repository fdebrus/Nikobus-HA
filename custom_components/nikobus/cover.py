"""Nikobus Cover entity."""

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
        self._position = 100 # Assume we start with open shutters
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
        if not self._in_motion:
            current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
            _LOGGER.debug(f"COVER _handle_coordinator_update {self._attr_name} {self._address} {self._channel} {current_state}")
            self._in_motion = current_state != 0x00
            self._is_opening = current_state == 0x01
            self._is_closing = current_state == 0x02
            if current_state == 0x01:
                self.hass.async_create_task(self._complete_movement(100))
            elif current_state == 0x02:
                self.hass.async_create_task(self._complete_movement(0))

    async def async_open_cover(self, **kwargs):
        _LOGGER.debug(f"{self._description} open cover")
        """Triggers the operation to fully open the cover."""
        self._is_closing = False
        self._is_opening = True
        self._in_motion = False
        await self._operate_cover(self._address, self._channel, "open")
        await self._complete_movement(100)

    async def async_close_cover(self, **kwargs):
        _LOGGER.debug(f"{self._address} {self._channel} close cover")
        """Triggers the operation to fully close the cover."""
        self._is_closing = True
        self._is_opening = False
        self._in_motion = False
        await self._operate_cover(self._address, self._channel, "close")
        await self._complete_movement(0)

    async def async_stop_cover(self):
        _LOGGER.debug(f"{self._address} {self._channel} stop cover at position {self._position}")
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        await self._dataservice.api.stop_cover(self._address, self._channel)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Sets the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)
        _LOGGER.info(f"Setting cover position to {target_position}")
        direction = "open" if target_position > self._position else "close"
        self._in_motion = False
        if direction == "open":
            self._is_opening = True
            self._is_closing = False
        else:
            self._is_opening = False
            self._is_closing = True
        await self._operate_cover(self._address, self._channel, direction)
        await self._complete_movement(target_position)

    async def _complete_movement(self, expected_position):
        _LOGGER.debug(f"{self._address} {self._channel} complete movement {expected_position}")
        """Completes the movement of the cover to the expected position."""
        position_diff = abs(self._position - expected_position)
        proportional_time_needed = (position_diff / 100.0) * self._operation_time
        if proportional_time_needed > 0:
            await self._update_position_in_real_time(expected_position, proportional_time_needed)

    async def _update_position_in_real_time(self, expected_position, total_time):
        _LOGGER.debug(f"{self._address} {self._channel} update real time target {expected_position} current {self._position} time needed {total_time} ")
        """Updates the cover's position in real time until the movement is completed."""
        start_time = datetime.now()
        initial_position = self._position
        direction = 1 if expected_position > initial_position else -1
        position_change = abs(expected_position - initial_position)

        self._in_motion = True

        while self._in_motion: 
            elapsed_time = (datetime.now() - start_time).total_seconds()
            _LOGGER.debug(f"{self._address} {self._channel} elapsed_time {elapsed_time} motion {self._in_motion} expected_position {expected_position} self._position {self._position}")
            if elapsed_time >= total_time:
                self._position = expected_position
                self._in_motion = False
                # if not self._nikobus_command:
                await self.async_stop_cover()
                break

            progress = elapsed_time / total_time
            self._position = initial_position + int(progress * position_change) if direction == 1 else initial_position - int(progress * position_change)
            self._position = max(0, min(100, self._position))

            _LOGGER.debug(f"{self._address} {self._channel} _position {self._position}")

            self.async_write_ha_state()
            await asyncio.sleep(0.5)

        self._is_opening = False
        self._is_closing = False
        self._nikobus_command = False
        self.async_write_ha_state()

    async def _operate_cover(self, address, channel, direction):
        _LOGGER.debug(f"{self._address} {self._channel} operate")
        if direction == 'open':
            await self._dataservice.api.open_cover(address, channel)
        else:
            await self._dataservice.api.close_cover(address, channel)
