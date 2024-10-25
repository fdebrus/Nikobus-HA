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

class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds):
        self.duration_in_seconds = duration_in_seconds
        self.start_time = None
        self.direction = None
        self.position = None
        _LOGGER.debug("PositionEstimator initialized with duration: %s seconds", duration_in_seconds)

    def start(self, direction, position):
        """Start the movement in the given direction."""
        self.direction = direction
        self.start_time = time.monotonic()
        self.position = position if position is not None else (0 if direction == "opening" else 100)
        _LOGGER.debug("Movement started in direction: %s, initial position: %s", direction, self.position)

    def get_position(self):
        """Calculate and return the current position estimate."""
        if self.start_time is None or self.direction is None or self.position is None:
            return None

        # Calculate elapsed time since the movement started
        elapsed_time = time.monotonic() - self.start_time
        progress = (elapsed_time / self.duration_in_seconds) * 100

        # Adjust the position based on the current direction
        if self.direction == "opening":
            new_position = min(100, self.position + progress)
        elif self.direction == "closing":
            new_position = max(0, self.position - progress)
        else:
            new_position = self.position

        # Clamp the position between 0 and 100
        new_position = max(0, min(100, int(new_position)))

        _LOGGER.debug("Position calculated to: %s based on elapsed time: %s seconds", new_position, elapsed_time)
        return new_position

    def stop(self):
        """Stop the movement and finalize the position."""
        if self.start_time is not None:
            self.position = self.get_position()

        self.direction = None
        self.start_time = None
        _LOGGER.debug("Movement stopped. Current estimated position: %s", self.position)

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
            channel.get("operation_time", "30"),
            channel.get("initial_position", None),
        )
        for address, cover_module_data in roller_modules.items()
        for i, channel in enumerate(cover_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Represents a Nikobus cover entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, operation_time, initial_position) -> None:
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

        self._operation_time = float(operation_time) if operation_time else None
        self._position_estimator = PositionEstimator(duration_in_seconds=float(operation_time))
        self._position = float(initial_position) if initial_position not in (None, '') else None

        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._movement_task = None
        self._is_button_initiated = False

        self._last_position_change_time = time.monotonic()

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
        if self._position is None:
            attributes['position'] = 'unknown'
        else:
            attributes['position'] = self._position
        return attributes

    @property
    def assumed_state(self):
        """Return True if the cover is in an assumed state."""
        return self._position is None

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        if self._position is None:
            _LOGGER.debug("Cover %s position is currently unknown.", self._attr_name)
            return 50  # Default to a midpoint (50) if the position is unknown for better estimation in UI.
        return self._position

    @property
    def is_open(self):
        """Return True if the cover is fully open."""
        if self._position is None:
            return None
        return self._position == 100

    @property
    def is_closed(self):
        """Return True if the cover is fully closed."""
        if self._position is None:
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

    async def async_added_to_hass(self):
        """Register callbacks when entity is added to hass."""
        await super().async_added_to_hass()

        # Subscribe to nikobus_button_pressed event
        self.hass.bus.async_listen('nikobus_button_pressed', self._handle_nikobus_button_event)

    @callback
    def _handle_nikobus_button_event(self, event):
        """Handle the nikobus_button_pressed event and update cover state."""
        address = event.data.get('address')
        button_operation_time = event.data.get('operation_time')
        impacted_module_address = event.data.get('impacted_module_address')

        _LOGGER.debug("*** handle_nikobus_button_event address : %s operation_time %s module %s***", address, button_operation_time, impacted_module_address)

        # Only proceed if the event address matches this cover's module address
        if impacted_module_address == self._address:
            _LOGGER.debug(f"Button pressed for cover {self._attr_name}, button operation_time: {button_operation_time}")

            self._is_button_initiated = True

            # Convert button_operation_time to a float if provided
            self._button_operation_time = float(button_operation_time) if button_operation_time else 0

            # Get the current state of the cover (opening, closing, or stopped)
            current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
            
            # Check if the cover is already in the desired motion state
            if self._in_motion and (
                (current_state == STATE_OPENING and self._direction == 'opening') or
                (current_state == STATE_CLOSING and self._direction == 'closing')
            ):
                _LOGGER.debug("Channel is already in the desired motion state, ignoring update")
                return

            # Update motion state and proceed with actions based on the current state
            if current_state == STATE_OPENING:
                _LOGGER.debug("Cover is opening")
                self._position_estimator.start('opening', self._position)
                self._is_opening = True
                self._is_closing = False
                self._in_motion = True
                self._direction = 'opening'
                self.async_write_ha_state()

            elif current_state == STATE_CLOSING:
                _LOGGER.debug("Cover is closing")
                self._position_estimator.start('closing', self._position)
                self._is_opening = False
                self._is_closing = True
                self._in_motion = True
                self._direction = 'closing'
                self.async_write_ha_state()

            elif current_state == STATE_STOPPED:
                _LOGGER.debug("Cover is stopped")
                self._position_estimator.stop()
                if self._movement_task is not None and not self._movement_task.done():
                    self._movement_task.cancel()
                self._is_opening = False
                self._is_closing = False
                self._in_motion = False
                self._direction = None
                self.async_write_ha_state()
                return

            if self._in_motion:
                if self._button_operation_time == 0:
                    _LOGGER.debug(f"Full movement detected, using cover operation time: {self._operation_time}")
                    self._button_operation_time = self._operation_time

                # Start real-time position update, passing duration for partial movement or None for full movement
                if not self._movement_task or self._movement_task.done():
                    self._movement_task = asyncio.create_task(
                        self._update_position_in_real_time(self._button_operation_time)
                    )

    async def _update_position_in_real_time(self, duration=None):
        """Periodically update the position of the cover during movement."""
        _LOGGER.debug(f"Starting real-time position updates for {self._attr_name} with duration: {duration}")

        start_time = time.monotonic()
        try:
            while self._in_motion:
                elapsed = time.monotonic() - start_time
                self._position = self._position_estimator.get_position()
                _LOGGER.debug("Real-time position update for %s: %s", self._attr_name, self._position)

                # Stop the movement if the duration is reached
                if duration and elapsed >= duration:
                    _LOGGER.debug(f"Operation time reached for {self._attr_name}. Stopping movement.")
                    # Trigger stop, respecting whether the movement is button-initiated or not
                    if self._is_button_initiated:
                        # Reset flag to allow stop command to be sent
                        self._is_button_initiated = False
                    await self.async_stop_cover()
                    break

                # Stop movement if target position is reached
                if (self._direction == 'opening' and self._position >= 100) or \
                    (self._direction == 'closing' and self._position <= 0):
                    _LOGGER.debug("Target position reached for %s. Stopping movement.", self._attr_name)
                    await self.async_stop_cover()
                    break

                # Update Home Assistant state with the current position
                self.async_write_ha_state()
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            _LOGGER.debug(f"Real-time position update for {self._attr_name} was cancelled")

        finally:
            # Reset the button initiation flag after movement completes
            self._is_button_initiated = False

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        _LOGGER.debug("Opening cover %s", self._attr_name)
        await self._start_movement('opening')

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        _LOGGER.debug("Closing cover %s", self._attr_name)
        await self._start_movement('closing')

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        _LOGGER.debug("Stopping cover %s", self._attr_name)

        # Send stop command to Nikobus if movement was initiated by Home Assistant or if duration was specified for a button press
        if not self._is_button_initiated:
            await self._dataservice.api.stop_cover(self._address, self._channel, self._direction)

        # Finalize the position estimate and stop all movement-related tasks
        self._position_estimator.stop()
        self._position = self._position_estimator.position

        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()

        # Reset motion and direction states
        self._is_opening = False
        self._is_closing = False
        self._in_motion = False
        self._direction = None

        # Update the Home Assistant state
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Set the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)

        # Debounce logic to avoid rapid commands
        current_time = time.monotonic()
        if current_time - self._last_position_change_time < 1:  # 1 second debounce window
            _LOGGER.debug("Skipping position update for %s due to rapid command frequency.", self._attr_name)
            return

        self._last_position_change_time = current_time

        _LOGGER.debug("Setting cover position for %s to: %d", self._attr_name, target_position)

        # Cancel and await previous movement task if it's still running
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        # If the position is unknown, assume a default midpoint (50%)
        if self._position is None:
            _LOGGER.debug("Position is unknown for %s. Assuming midpoint (50)", self._attr_name)
            self._position = 50  # Assume a midpoint if unknown

        # Determine direction based on the target vs. current position
        delta = target_position - self._position
        if delta == 0:
            _LOGGER.debug("Cover %s is already at the target position.", self._attr_name)
            return

        # Direction logic
        self._direction = 'opening' if delta > 0 else 'closing'
        self._is_opening = self._direction == 'opening'
        self._is_closing = self._direction == 'closing'

        # Calculate time to move based on the delta and duration
        time_to_move = abs(delta) * (self._position_estimator.duration_in_seconds / 100)

        _LOGGER.debug("Cover %s will move for %s seconds in direction: %s", self._attr_name, time_to_move, self._direction)

        # Start the position estimation and movement
        self._position_estimator.start(self._direction, self._position)

        # Start the actual cover movement by sending the command to the device
        await self._operate_cover()

        # Schedule a task to update the position in real time while moving
        self._movement_task = asyncio.create_task(self._update_position_to_target(target_position))

    async def _update_position_to_target(self, target_position):
        """Periodically update the position of the cover until it reaches the target position."""
        _LOGGER.debug("Starting position update to target %d for %s", target_position, self._attr_name)
        try:
            while self._in_motion:
                # Update the estimated position
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
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            _LOGGER.debug("Position update to target for %s was cancelled", self._attr_name)

    async def _start_movement(self, direction):
        """Start movement in the specified direction."""
        if self._in_motion:
            await self.async_stop_cover()

        self._direction = direction
        self._is_opening = direction == 'opening'
        self._is_closing = direction == 'closing'
        self._in_motion = True
        self._position_estimator.start(direction, self._position)

        await self._operate_cover()

        if not self._movement_task or self._movement_task.done():
            self._movement_task = asyncio.create_task(self._update_position_in_real_time())

    async def _operate_cover(self):
        """Send the command to operate the cover."""
        _LOGGER.debug("Operating cover %s in direction: %s", self._attr_name, self._direction)

        if self._direction == 'opening':
            await self._dataservice.api.open_cover(self._address, self._channel)
        elif self._direction == 'closing':
            await self._dataservice.api.close_cover(self._address, self._channel)

        self._in_motion = True
        self.async_write_ha_state()
