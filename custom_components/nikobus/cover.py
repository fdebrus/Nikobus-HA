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
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
FULL_OPERATION_BUFFER = 3

class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds):
        self._duration_in_seconds = duration_in_seconds
        self._start_time = None
        self._direction = None
        self.position = None
        _LOGGER.debug("PositionEstimator initialized with duration: %s seconds", duration_in_seconds)

    def start(self, direction, position=None):
        """Start the movement in the given direction."""
        self._direction = 1 if direction == "opening" else -1
        self._start_time = time.monotonic()
        self.position = position if position is not None else (0 if self._direction == 1 else 100)
        _LOGGER.debug("Movement started in direction: %s, initial position: %s", direction, self.position)

    def get_position(self):
        """Calculate and return the current position estimate."""
        if self._start_time is None or self._direction is None or self.position is None:
            return None

        elapsed_time = time.monotonic() - self._start_time
        progress = (elapsed_time / self._duration_in_seconds) * 100 * self._direction
        new_position = max(0, min(100, self.position + progress))

        _LOGGER.debug("Position calculated to: %s based on elapsed time: %s seconds", new_position, elapsed_time)
        return int(new_position)

    def stop(self):
        """Stop the movement and finalize the position."""
        if self._start_time is not None:
            self.position = self.get_position()
        self._direction = None
        self._start_time = None
        _LOGGER.debug("Movement stopped. Current estimated position: %s", self.position)

    @property
    def duration_in_seconds(self):
        """Publicly expose the duration_in_seconds attribute."""
        return self._duration_in_seconds

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
        )
        for address, cover_module_data in roller_modules.items()
        for i, channel in enumerate(cover_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity, RestoreEntity):
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

        self._operation_time = float(operation_time) if operation_time else None
        self._position_estimator = PositionEstimator(duration_in_seconds=float(operation_time))
        self._position = 100

        self._button_operation_time = None

        self._in_motion = False
        self._movement_task = None

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
        attributes['position'] = self._position
        return attributes

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self._position

    @property
    def is_open(self):
        """Return True if the cover is fully open."""
        return self._position == 100

    @property
    def is_closed(self):
        """Return True if the cover is fully closed."""
        return self._position == 0

    @property
    def is_opening(self):
        """Return True if the cover is currently opening."""
        return self._in_motion and self._direction == 'opening'

    @property
    def is_closing(self):
        """Return True if the cover is currently closing."""
        return self._in_motion and self._direction == 'closing'

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

        # Restore the previous state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            last_position = last_state.attributes.get(ATTR_POSITION)
            if last_position is not None:
                self._position = float(last_position)
                _LOGGER.debug("Restored position for %s to %s", self._attr_name, self._position)
            else:
                _LOGGER.debug("Last position is None for %s. Using default position %s", self._attr_name, self._position)
        else:
            _LOGGER.debug("No last state available for %s. Using default position %s", self._attr_name, self._position)

        # Initialize previous state from current API state
        current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
        self._previous_state = current_state
        _LOGGER.debug("Initialized previous state for %s to %s", self._attr_name, self._previous_state)

        # Subscribe to nikobus_button_pressed event
        self.hass.bus.async_listen('nikobus_button_pressed', self._handle_nikobus_button_event)

    async def _wait_for_movement_task(self):
        if self._movement_task is not None:
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

    @callback
    def _handle_nikobus_button_event(self, event):
        """Handle the nikobus_button_pressed event and update cover state."""
        address = event.data.get('address')
        button_operation_time = event.data.get('operation_time', None)
        impacted_module_address = event.data.get('impacted_module_address')

        _LOGGER.debug("*** handle_nikobus_button_event address: %s operation_time: %s module: %s ***",
                      address, button_operation_time, impacted_module_address)

        # Only proceed if the event address matches this cover's module address
        if impacted_module_address == self._address:
            # Get the current state for this cover's channel
            current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
            _LOGGER.debug("Current state for cover %s (channel %d): %s", self._attr_name, self._channel, current_state)

            # Compare with the previous state
            if current_state != self._previous_state:
                _LOGGER.debug("State change detected for cover %s (channel %d)", self._attr_name, self._channel)
                self._previous_state = current_state

                # Set operation time if provided
                if button_operation_time:
                    _LOGGER.debug("Button operation_time received: %s", button_operation_time)
                    self._button_operation_time = float(button_operation_time)

                # Handle the new state
                if current_state == STATE_OPENING:
                    _LOGGER.debug("Cover %s is opening due to button press.", self._attr_name)
                    self._position_estimator.start('opening', self._position)
                    self._in_motion = True
                    self._direction = 'opening'

                    # Start real-time position updates if not already running
                    if not self._movement_task or self._movement_task.done():
                        self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

                elif current_state == STATE_CLOSING:
                    _LOGGER.debug("Cover %s is closing due to button press.", self._attr_name)
                    self._position_estimator.start('closing', self._position)
                    self._in_motion = True
                    self._direction = 'closing'

                    # Start real-time position updates if not already running
                    if not self._movement_task or self._movement_task.done():
                        self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

                elif current_state == STATE_STOPPED:
                    _LOGGER.debug("Cover %s has stopped due to button press.", self._attr_name)
                    self._position_estimator.stop()
                    self._position = self._position_estimator.position if self._position_estimator.position is not None else self._position
                    self._in_motion = False
                    self._direction = None

                    # Cancel the movement task if it's running
                    if self._movement_task is not None and not self._movement_task.done():
                        self._movement_task.cancel()
                        self.hass.async_create_task(self._wait_for_movement_task())

                # Update the Home Assistant state
                self.async_write_ha_state()
            else:
                _LOGGER.debug("No state change detected for cover %s (channel %d)", self._attr_name, self._channel)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        current_state = self._dataservice.api.get_cover_state(self._address, self._channel)
        _LOGGER.debug("Coordinator update received for %s. Current state: %s Position: %s", self._attr_name, current_state, self._position)

        if current_state == self._previous_state:
            _LOGGER.debug("No state change detected for %s. Skipping update.", self._attr_name)
            return

        self._previous_state = current_state

        if current_state == STATE_STOPPED:
            _LOGGER.debug("Cover %s is stopped.", self._attr_name)
            self._position_estimator.stop()

            # Cancel the movement task if it's running
            if self._movement_task is not None and not self._movement_task.done():
                self._movement_task.cancel()
                self.hass.async_create_task(self._wait_for_movement_task())

            self._position = self._position_estimator.position if self._position_estimator.position is not None else self._position
            self._in_motion = False
            self._direction = None
            self.async_write_ha_state()
            return

        if current_state == STATE_OPENING:
            _LOGGER.debug("Cover %s is opening.", self._attr_name)
            self._position_estimator.start('opening', self._position)
            self._in_motion = True
            self._direction = 'opening'
            self.async_write_ha_state()

        elif current_state == STATE_CLOSING:
            _LOGGER.debug("Cover %s is closing.", self._attr_name)
            self._position_estimator.start('closing', self._position)
            self._in_motion = True
            self._direction = 'closing'
            self.async_write_ha_state()

        if not self._movement_task or self._movement_task.done():
            # Schedule the task to update position in real-time
            self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

    async def _update_position_in_real_time(self):
        """Periodically update the position of the cover during movement."""
        _LOGGER.debug(f"Starting real-time position updates for {self._attr_name}")

        start_time = time.monotonic()
        buffer_time_active = False  # Tracks if the extra buffer time is in effect
        buffer_start_time = None    # Records the start time of the buffer period

        try:
            while self._in_motion:
                elapsed = time.monotonic() - start_time
                self._position = self._position_estimator.get_position()
                _LOGGER.debug("Real-time position update for %s: %s", self._attr_name, self._position)

                # Stop the movement if the button duration is reached
                if self._button_operation_time and elapsed >= self._button_operation_time:
                    _LOGGER.debug(f"Button operation time reached for {self._attr_name}. Stopping movement.")
                    await self.async_stop_cover()
                    break

                # Check if target position is reached
                if (self._direction == 'opening' and self._position >= 100) or \
                (self._direction == 'closing' and self._position <= 0):

                    _LOGGER.debug(f"Target position reached for {self._attr_name}. Updating state.")

                    # Update position to the exact target
                    self._position = 100 if self._direction == 'opening' else 0

                    # Update Home Assistant state with the current position
                    self.async_write_ha_state()

                    if not buffer_time_active:
                        buffer_time_active = True
                        buffer_start_time = time.monotonic()
                        _LOGGER.debug("Initiating extra buffer time for %s.", self._attr_name)
                    elif time.monotonic() - buffer_start_time >= FULL_OPERATION_BUFFER:
                        _LOGGER.debug("Extra buffer time completed for %s. Stopping movement.", self._attr_name)
                        await self.async_stop_cover()
                        break  # Exit the loop after stopping the cover
                else:
                    # If still in motion, update the HA state
                    self.async_write_ha_state()

                # If the cover has been stopped elsewhere, exit the loop
                if not self._in_motion:
                    _LOGGER.debug(f"Cover {self._attr_name} is no longer in motion. Exiting position update loop.")
                    break

                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            _LOGGER.debug(f"Real-time position update for {self._attr_name} was cancelled")

        finally:
            # Reset buffer tracking variables when the movement ends
            buffer_time_active = False
            buffer_start_time = None

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

        await self._dataservice.api.stop_cover(self._address, self._channel, self._direction)

        # Finalize the position estimate and stop all movement-related tasks
        self._position_estimator.stop()
        self._position = self._position_estimator.position

        # Reset motion and direction states
        self._in_motion = False
        self._direction = None

        self._button_operation_time = None

        # Cancel the movement task if it's running
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Set the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)

        self._button_operation_time = None

        # Debounce logic to avoid rapid commands
        current_time = time.monotonic()
        if current_time - self._last_position_change_time < 1:  # 1 second debounce window, eg HomeKit commands
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

        # Determine direction based on the target vs. current position
        delta = target_position - self._position
        if delta == 0:
            _LOGGER.debug("Cover %s is already at the target position.", self._attr_name)
            return

        # Direction logic
        self._direction = 'opening' if delta > 0 else 'closing'

        # Calculate time to move based on the delta and duration
        time_to_move = abs(delta) * (self._position_estimator.duration_in_seconds / 100)

        _LOGGER.debug("Cover %s will move for %s seconds in direction: %s", self._attr_name, time_to_move, self._direction)

        # Start the position estimation and movement
        self._position_estimator.start(self._direction, self._position)

        # Start the actual cover movement by sending the command to the device
        await self._operate_cover()

        # Schedule a task to update the position in real time while moving
        self._movement_task = self.hass.async_create_task(self._update_position_to_target(target_position))

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
            # Stop the current movement
            await self.async_stop_cover()

        # Set the new direction and motion state
        self._direction = direction
        self._in_motion = True
        self.async_write_ha_state()

        self._button_operation_time = None
        self._position_estimator.start(direction, self._position)

        await self._operate_cover()

        if not self._movement_task or self._movement_task.done():
            self._movement_task = self.hass.async_create_task(self._update_position_in_real_time())

    async def _operate_cover(self):
        """Send the command to operate the cover."""
        _LOGGER.debug("Operating cover %s in direction: %s", self._attr_name, self._direction)

        if self._direction == 'opening':
            await self._dataservice.api.open_cover(self._address, self._channel)
        elif self._direction == 'closing':
            await self._dataservice.api.close_cover(self._address, self._channel)

        self._in_motion = True
        self.async_write_ha_state()
