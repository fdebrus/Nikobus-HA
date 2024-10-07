import logging
import asyncio
import time
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

STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02

UNKNOWN_POSITION = None

class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds):
        self.duration_in_seconds = duration_in_seconds
        self.start_time = None
        self.direction = None
        self.initial_position = UNKNOWN_POSITION  # Start with unknown position

        _LOGGER.debug("PositionEstimator initialized with duration: %s seconds", duration_in_seconds)

    def start(self, direction, initial_position):
        """Start the movement in the given direction."""
        self.direction = direction
        self.start_time = time.monotonic()
        self.initial_position = initial_position
        _LOGGER.debug("Movement started in direction: %s, initial position: %s", direction, initial_position)

    def get_position(self):
        """Calculate and return the current position estimate."""
        if self.start_time is None or self.direction is None:
            # If there's no start time or direction, keep the current initial position if it's known
            return self.initial_position

        # Calculate elapsed time since the movement started
        elapsed_time = time.monotonic() - self.start_time
        progress = (elapsed_time / self.duration_in_seconds) * 100

        # Adjust the position based on the current direction
        if self.direction == "opening":
            new_position = min(100, self.initial_position + progress)
        elif self.direction == "closing":
            new_position = max(0, self.initial_position - progress)
        else:
            new_position = self.initial_position

        # Clamp the position between 0 and 100
        new_position = max(0, min(100, int(new_position)))

        _LOGGER.debug("Position calculated to: %s based on elapsed time: %s seconds", new_position, elapsed_time)
        return new_position

    def stop(self):
        """Stop the movement and finalize the position."""
        if self.start_time is not None:
            self.initial_position = self.get_position()
        self.direction = None
        self.start_time = None
        _LOGGER.debug("Movement stopped. Current estimated position: %s", self.initial_position)


async def async_setup_entry(hass, entry, async_add_entities) -> bool:
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

    async_add_entities(entities)


class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Represents a Nikobus cover entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time) -> None:
        """Initialize the cover entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self.hass = hass
        self._dataservice = dataservice
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._direction = None
        self._previous_state = None

        self._position_estimator = PositionEstimator(duration_in_seconds=float(operation_time))
        self._position = UNKNOWN_POSITION  # Start with unknown position

        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._movement_task = None

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

        _LOGGER.debug("NikobusCoverEntity initialized for %s (address: %s, channel: %s)", channel_description, address, channel)

    @property
    def device_info(self):
        """Provide device information for Home Assistant."""
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
        if self._position is UNKNOWN_POSITION:
            attributes['position'] = 'unknown'
        else:
            attributes['position'] = self._position
        return attributes

    @property
    def assumed_state(self):
        """Return True if the cover is in an assumed state."""
        return self._position is UNKNOWN_POSITION

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        if self._position is UNKNOWN_POSITION:
            return None  # Representing an unknown state in the UI
        return self._position

    @property
    def is_open(self):
        """Return True if the cover is fully open."""
        if self._position is UNKNOWN_POSITION:
            return None
        return self._position == 100

    @property
    def is_closed(self):
        """Return True if the cover is fully closed."""
        if self._position is UNKNOWN_POSITION:
            return None
        return self._position == 0

    @property
    def is_opening(self):
        """Return True if the cover is currently opening."""
        return self._is_opening

    @property
    def is_closing(self):
        """Return True if the cover is currently closing."""
        return self._is_closing

    @property
    def supported_features(self):
        """Return supported features."""
        return (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
        _LOGGER.debug("Coordinator update received for %s. Current state: %s", self._attr_name, current_state)

        if current_state == self._previous_state:
            _LOGGER.debug("No state change detected for %s. Skipping update.", self._attr_name)
            return

        self._previous_state = current_state

        if current_state == STATE_STOPPED:
            _LOGGER.debug("Cover %s has stopped.", self._attr_name)
            self._position_estimator.stop()
            self._position = self._position_estimator.get_position()
            self._is_opening = False
            self._is_closing = False
            self._in_motion = False
            self._direction = None
            self.async_write_ha_state()
            return

        # Only assign a starting position when movement begins
        if current_state == STATE_OPENING:
            _LOGGER.debug("Cover %s is opening.", self._attr_name)
            self._position_estimator.start('opening', self._position if self._position is not UNKNOWN_POSITION else 50)
            self._is_opening = True
            self._is_closing = False
        elif current_state == STATE_CLOSING:
            _LOGGER.debug("Cover %s is closing.", self._attr_name)
            self._position_estimator.start('closing', self._position if self._position is not UNKNOWN_POSITION else 50)
            self._is_opening = False
            self._is_closing = True

        self._in_motion = True
        self._direction = 'opening' if current_state == STATE_OPENING else 'closing'
        self.async_write_ha_state()

        # Ensure that the real-time update is started if movement starts
        if not self._movement_task or self._movement_task.done():
            self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

    async def _update_position_in_real_time(self):
        """Periodically update the position of the cover during movement."""
        _LOGGER.debug("Starting real-time position updates for %s", self._attr_name)
        try:
            await asyncio.sleep(0.1)  # Give the cover time to start moving
            while self._in_motion:
                self._position = self._position_estimator.get_position()
                _LOGGER.debug("Real-time position update for %s: %s", self._attr_name, self._position)

                # Stop updating if the cover has reached the target position (0 or 100)
                if ((self._direction == 'opening' and self._position >= 100) or
                    (self._direction == 'closing' and self._position <= 0)):
                    _LOGGER.debug("Target position reached for %s. Sending stop command.", self._attr_name)
                    await self.async_stop_cover()  # Stop the cover when the target is reached

                self.async_write_ha_state()
                await asyncio.sleep(0.5)  # Update position every 0.5 seconds for real-time effect
        except asyncio.CancelledError:
            _LOGGER.debug("Real-time position update for %s was cancelled", self._attr_name)

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        _LOGGER.debug("Opening cover %s", self._attr_name)

        if self._in_motion:
            await self.async_stop_cover()

        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        self._direction = 'opening'
        self._is_opening = True
        self._is_closing = False
        self._in_motion = True
        self._position_estimator.start('opening', self._position if self._position is not UNKNOWN_POSITION else 50)
        await self._operate_cover()

        # Start real-time position updates
        if not self._movement_task or self._movement_task.done():
            self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        _LOGGER.debug("Closing cover %s", self._attr_name)

        if self._in_motion:
            await self.async_stop_cover()

        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        self._direction = 'closing'
        self._is_opening = False
        self._is_closing = True
        self._in_motion = True
        self._position_estimator.start('closing', self._position if self._position is not UNKNOWN_POSITION else 50)
        await self._operate_cover()

        # Start real-time position updates
        if not self._movement_task or self._movement_task.done():
            self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        _LOGGER.debug("Stopping cover %s", self._attr_name)

        # Send the stop command to the device
        await self._dataservice.api.stop_cover(self._address, self._channel, self._direction)

        # Cancel any active movement task
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        # Update the position based on the current elapsed time
        self._position_estimator.stop()
        self._position = self._position_estimator.get_position()  # Keep the calculated position instead of setting it to None

        # Reset movement-related states
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._direction = None

        # Write the updated state to Home Assistant
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Set the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)

        if target_position is None:
            _LOGGER.warning("No target position specified for %s.", self._attr_name)
            return

        _LOGGER.debug("Setting cover position for %s to: %d", self._attr_name, target_position)

        # Cancel any existing movement
        if self._in_motion:
            await self.async_stop_cover()

        # Determine the direction based on target position and start movement
        if self._position is None:
            # If the current position is unknown, assume the movement direction based on target position
            if target_position > 50:
                self._direction = 'opening'
                self._is_opening = True
                self._is_closing = False
            else:
                self._direction = 'closing'
                self._is_opening = False
                self._is_closing = True
            # Keep position as unknown until real-time updates kick in
            _LOGGER.debug("Current position for %s is unknown. Initiating movement towards target %d.", self._attr_name, target_position)
        else:
            # If current position is known, determine direction normally
            if target_position > self._position:
                self._direction = 'opening'
                self._is_opening = True
                self._is_closing = False
            else:
                self._direction = 'closing'
                self._is_opening = False
                self._is_closing = True

        self._in_motion = True
        self._position_estimator.start(self._direction, self._position if self._position is not None else 50)

        # Start real-time position updates and movement task
        await self._operate_cover()

        if not self._movement_task or self._movement_task.done():
            self._movement_task = self.hass.async_create_task(self._update_position_to_target(target_position))

    async def _update_position_to_target(self, target_position):
        """Periodically update the position of the cover until it reaches the target position."""
        _LOGGER.debug("Starting position update to target %d for %s", target_position, self._attr_name)
        try:
            while self._in_motion:
                # Update the estimated position
                if self._position is None:
                    self._position = 50  # Start estimating from midpoint if position is unknown

                self._position = self._position_estimator.get_position()
                _LOGGER.debug("Real-time position update for %s: %s", self._attr_name, self._position)

                # Check if the cover has reached or passed the target position
                if ((self._direction == 'opening' and self._position >= target_position) or
                        (self._direction == 'closing' and self._position <= target_position)):
                    _LOGGER.debug("Target position %d reached for %s. Stopping movement.", target_position, self._attr_name)
                    await self.async_stop_cover()
                    self._position = target_position  # Ensure position is set exactly to target
                    self.async_write_ha_state()
                    return

                self.async_write_ha_state()
                await asyncio.sleep(0.5)  # Update position every 0.5 seconds for real-time effect
        except asyncio.CancelledError:
            _LOGGER.debug("Position update to target for %s was cancelled", self._attr_name)

    async def _operate_cover(self):
        """Send the command to operate the cover."""
        self._in_motion = True
        if self._direction == 'opening':
            self._is_opening = True
            self._is_closing = False
            await self._dataservice.api.open_cover(self._address, self._channel)
        elif self._direction == 'closing':
            self._is_opening = False
            self._is_closing = True
            await self._dataservice.api.close_cover(self._address, self._channel)

        _LOGGER.debug("Operating cover %s in direction: %s", self._attr_name, self._direction)
        self.async_write_ha_state()
