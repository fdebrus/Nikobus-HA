import logging
import asyncio
from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass,
    ATTR_POSITION,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND, COVER_DELAY_BEFORE_STOP

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    _LOGGER.debug("Setting up entry: %s", entry.entry_id)
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    roller_modules = dataservice.api.dict_module_data.get('roller_module', {})

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
        for address, cover_module_data in roller_modules.items()
        for i, channel in enumerate(cover_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    _LOGGER.debug("Entities created: %s", entities)

    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Represents a Nikobus cover entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time) -> None:
        """Initialize the cover entity with data from the Nikobus system configuration."""
        _LOGGER.debug("Initializing cover entity: %s", description)
        super().__init__(dataservice)
        self._state = None
        self.hass = hass
        self._dataservice = dataservice
        self._position = 100  # Assume we start with open shutters
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._movement_task = None
        self._operation_time = float(operation_time)  # Operation time in seconds to fully open/close the cover.
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._direction = None

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

    @property
    def device_info(self):
        """Provide device information for Home Assistant."""
        _LOGGER.debug("Device info requested for: %s", self._description)
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attributes = super().extra_state_attributes or {}
        if self._state == 'error':
            attributes['error_state'] = '0x03 - Conflict detected or unknown state'
        return attributes

    @property
    def assumed_state(self):
        """Return True if the cover is in an assumed state."""
        return self._state == 'error'

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        _LOGGER.debug("Current cover position: %d", self._position)
        return self._position

    @property
    def is_open(self):
        """Return True if the cover is fully open."""
        if self._state == 'error':
            return None
        _LOGGER.debug("Is cover open? %s", self._position == 100)
        return self._position == 100

    @property
    def is_closed(self):
        """Return True if the cover is fully closed."""
        if self._state == 'error':
            return None 
        _LOGGER.debug("Is cover closed? %s", self._position == 0)
        return self._position == 0

    @property
    def supported_features(self):
        """Return supported features."""
        return (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )

    @property
    def is_opening(self):
        """Return True if the cover is opening."""
        _LOGGER.debug("Is cover opening? %s", self._is_opening)
        return self._is_opening

    @property
    def is_closing(self):
        """Return True if the cover is closing."""
        _LOGGER.debug("Is cover closing? %s", self._is_closing)
        return self._is_closing
        
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        
        _LOGGER.debug("Handling coordinator update.")      
        
        source = self._dataservice.get_update_source()
        
        _LOGGER.debug("**** SOURCE: %s", source)

        if not source == "api_call":
            current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
        
            _LOGGER.debug("**** %s Current cover state: 0x%X", self._attr_name, current_state)

            # Cancel the current movement task if it's still running
            if self._movement_task is not None and not self._movement_task.done():
                _LOGGER.debug("Cancelling ongoing movement task")
                self._movement_task.cancel()

                def task_cleanup(task):
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        _LOGGER.debug("Movement task was successfully cancelled.")
                    except Exception as e:
                        _LOGGER.error("Error during movement task cleanup: %s", e)

                # Schedule the task cleanup asynchronously
                self._movement_task.add_done_callback(task_cleanup)

            # Update motion state based on current state
            self._in_motion = current_state != 0x00
            self._is_opening = current_state == 0x01
            self._is_closing = current_state == 0x02

            if current_state not in (0x00, 0x01, 0x02):
                _LOGGER.warning(
                    "Cover %s is in an unknown or error state (0x%X)",
                    self._attr_name,
                    current_state
                )
                self._is_opening = False
                self._is_closing = False
                self._in_motion = False
                self._state = 'error'
                self._direction = None
            else:
                self._state = None

            _LOGGER.debug("**** %s Current cover state: in motion %s is opening %s is closing %s state %s", self._attr_name, self._in_motion, self._is_opening, self._is_closing, self._state)

            # Create tasks for the movement
            if current_state == 0x01 and self._position != 100:
                self.hass.async_create_task(self._complete_movement(100))
            elif current_state == 0x02 and self._position != 0:
                self.hass.async_create_task(self._complete_movement(0))
            else:
                self._is_opening = False
                self._is_closing = False
                self._in_motion = False

            # Write the updated state to Home Assistant
            self.async_write_ha_state()

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        _LOGGER.debug("Opening cover.")
        self._direction = 'opening'
        await self._operate_cover()
        self._movement_task = asyncio.create_task(self._complete_movement(100))

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        _LOGGER.debug("Closing cover.")
        self._direction = 'closing'
        await self._operate_cover()
        self._movement_task = asyncio.create_task(self._complete_movement(0))

    async def async_set_cover_position(self, **kwargs):
        """Set the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)
        _LOGGER.debug("Setting cover position to: %d", target_position)
        self._is_opening = False
        self._is_closing = False
        if self._in_motion:
            self._in_motion = False
            await self.async_stop_cover()
        if target_position > self._position:
            self._direction = 'opening'
            await self._operate_cover()
            self._movement_task = asyncio.create_task(self._complete_movement(target_position))
        else:
            self._direction = 'closing'
            await self._operate_cover()
            self._movement_task = asyncio.create_task(self._complete_movement(target_position))

    async def _complete_movement(self, expected_position):
        """Complete the movement to the expected position."""
        _LOGGER.debug("Completing movement to position: %d", expected_position)
        position_diff = abs(self._position - expected_position)
        total_time = (position_diff / 100.0) * self._operation_time
        if total_time > 0:
            await self._update_position_in_real_time(expected_position, total_time)

    async def _update_position_in_real_time(self, expected_position, total_time):
        """Update the cover's position in real time."""
        _LOGGER.debug("Updating position in real time to: %d over %f seconds", expected_position, total_time)
        start_time = self.hass.loop.time()
        initial_position = self._position
        position_change = abs(expected_position - initial_position)
        direction = 1 if expected_position > initial_position else -1

        while self._in_motion:
            try:
                elapsed_time = self.hass.loop.time() - start_time
                progress = min(elapsed_time / total_time, 1.0)
                self._position = initial_position + direction * progress * position_change
                self._position = max(0, min(100, int(self._position)))
                self.async_write_ha_state()

                if ((direction == 1 and self._position >= expected_position) or
                    (direction == -1 and self._position <= expected_position)):
                    self._position = expected_position
                    self._is_opening = False
                    self._is_closing = False
                    self._in_motion = False
                    self.async_write_ha_state()

                    if expected_position in [0, 100]:
                        await asyncio.sleep(COVER_DELAY_BEFORE_STOP)
                        await self.async_stop_cover()
                    else:
                        await self.async_stop_cover()
                    break

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                _LOGGER.debug("Movement operation cancelled during real-time update.")
                break

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        _LOGGER.debug("Stopping cover.")
        await self._dataservice.api.stop_cover(self._address, self._channel, self._direction)
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task was cancelled.")
        self.async_write_ha_state()

    async def _operate_cover(self):
        """Send the command to operate the cover."""
        _LOGGER.debug("Operating cover with direction: %s", self._direction)
        self._in_motion = True
        if self._direction == 'opening':
            self._is_opening = True
            self._is_closing = False
            await self._dataservice.api.open_cover(self._address, self._channel)
        elif self._direction == 'closing':
            self._is_opening = False
            self._is_closing = True
            await self._dataservice.api.close_cover(self._address, self._channel)
