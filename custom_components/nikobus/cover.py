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
STATE_UNKNOWN = 0x03
FULL_OPERATION_BUFFER = 3


class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds, start_position):
        self._duration_in_seconds = duration_in_seconds
        self._start_time = None
        self._direction = None
        self.position = start_position
        _LOGGER.debug(
            "PositionEstimator initialized with duration: %s seconds",
            duration_in_seconds,
        )

    def start(self, direction, position=None):
        """Start the movement in the given direction."""
        new_direction = 1 if direction == "opening" else -1
        if self._start_time is not None:
            if self._direction == new_direction:
                _LOGGER.debug(
                    "PositionEstimator.start() called but already started in the same direction. Ignoring."
                )
                return
            else:
                _LOGGER.debug(
                    "PositionEstimator.start() called with new direction. Restarting estimator."
                )
                self.stop()

        self._direction = new_direction
        self._start_time = time.monotonic()
        if position is not None:
            self.position = position
        else:
            self.position = 0 if self._direction == 1 else 100
        _LOGGER.debug(
            "Movement started in direction: %s, initial position: %s",
            direction,
            self.position,
        )

    def get_position(self):
        """Calculate and return the current position estimate."""
        if self._start_time is None or self._direction is None or self.position is None:
            return None

        elapsed_time = time.monotonic() - self._start_time
        progress = (elapsed_time / self._duration_in_seconds) * 100 * self._direction
        new_position = max(0, min(100, self.position + progress))

        _LOGGER.debug(
            "Position calculated to: %s based on elapsed time: %s seconds",
            new_position,
            elapsed_time,
        )
        return int(new_position)

    def stop(self):
        """Stop the movement and finalize the position."""
        if self._start_time is not None:
            estimated_position = self.get_position()
            if estimated_position is not None:
                self.position = estimated_position
            else:
                _LOGGER.debug(
                    "PositionEstimator.get_position() returned None during stop."
                )
        else:
            _LOGGER.debug("PositionEstimator.stop() called but _start_time is None.")
        self._direction = None
        self._start_time = None
        _LOGGER.debug("Movement stopped. Current estimated position: %s", self.position)

    @property
    def duration_in_seconds(self):
        """Publicly expose the duration_in_seconds attribute."""
        return self._duration_in_seconds


async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    """Set up Nikobus cover entities from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    roller_modules = coordinator.dict_module_data.get("roller_module", {})

    entities = [
        NikobusCoverEntity(
            hass,
            coordinator,
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

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        description,
        model,
        address,
        channel,
        channel_description,
        operation_time,
    ) -> None:
        """Initialize the cover entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self.hass = hass
        self._coordinator = coordinator
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._direction = None
        self._state = STATE_STOPPED
        self._position = 100  # Default position is fully open
        self._previous_state = None

        self._operation_time = float(operation_time) if operation_time else 30.0
        self._position_estimator = PositionEstimator(
            duration_in_seconds=self._operation_time, start_position=self._position
        )

        self._button_operation_time = None

        self._in_motion = False
        self._movement_task = None

        self._last_position_change_time = time.monotonic()

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

        _LOGGER.debug(
            "NikobusCoverEntity initialized for %s (address: %s, channel: %s)",
            channel_description,
            address,
            channel,
        )

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
        attributes["position"] = self._position
        attributes["state"] = self._state
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
        return self._state == STATE_OPENING

    @property
    def is_closing(self):
        """Return True if the cover is currently closing."""
        return self._state == STATE_CLOSING

    @property
    def available(self):
        """Indicate whether the cover is available."""
        return self._state != STATE_UNKNOWN

    @property
    def supported_features(self):
        """Return supported features."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
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
                _LOGGER.debug(
                    "Restored position for %s to %s", self._attr_name, self._position
                )
            else:
                _LOGGER.debug(
                    "Last position is None for %s. Using default position 100",
                    self._attr_name,
                )
                self._position = 100  # Ensure default is set
        else:
            _LOGGER.debug(
                "No last state available for %s. Using default position 100",
                self._attr_name,
            )
            self._position = 100  # Ensure default is set

        # Initialize state from current API state
        self._state = self._coordinator.get_cover_state(self._address, self._channel)
        _LOGGER.debug(
            "Initialized state for %s to %s",
            self._attr_name,
            self._state,
        )

        # Initialize previous state
        self._previous_state = self._state

        # Subscribe to nikobus_button_pressed event
        self.hass.bus.async_listen(
            "nikobus_button_pressed", self._handle_nikobus_button_event
        )
        self.async_write_ha_state()

    async def _wait_for_movement_task(self):
        if self._movement_task is not None:
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

    @callback
    def _handle_nikobus_button_event(self, event):
        """Handle the nikobus_button_pressed event and update cover state."""
        impacted_module_address = event.data.get("impacted_module_address")

        # Only proceed if the event address matches this cover's module address
        if impacted_module_address == self._address:
            # Get the current state for this cover's channel
            new_state = self._coordinator.get_cover_state(
                self._address, self._channel
            )
            self._process_state_change(new_state)
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        new_state = self._coordinator.get_cover_state(self._address, self._channel)
        self._process_state_change(new_state)
        self.async_write_ha_state()

    def _process_state_change(self, new_state):
        """Process the state change for the cover."""
        if new_state == self._previous_state:
            _LOGGER.debug(
                f"No state change detected for {self._attr_name}; skipping processing."
            )
            return

        _LOGGER.debug(
            f"State changed from {self._previous_state} to {new_state} for {self._attr_name}"
        )
        self._previous_state = new_state
        self._state = new_state

        if new_state == STATE_OPENING:
            self._direction = "opening"
            self._in_motion = True
            self._position_estimator.start(self._direction, self._position)
            if not self._movement_task or self._movement_task.done():
                self._movement_task = self.hass.async_create_task(
                    self._update_position()
                )

        elif new_state == STATE_CLOSING:
            self._direction = "closing"
            self._in_motion = True
            self._position_estimator.start(self._direction, self._position)
            if not self._movement_task or self._movement_task.done():
                self._movement_task = self.hass.async_create_task(
                    self._update_position()
                )

        elif new_state == STATE_STOPPED:
            if self._position_estimator._start_time is not None:
                self._position_estimator.stop()
                if self._position_estimator.position is not None:
                    self._position = self._position_estimator.position
                else:
                    _LOGGER.warning(
                        f"Position estimator returned None for position in _process_state_change for {self._attr_name}"
                    )
            else:
                _LOGGER.debug(
                    f"Position estimator was not started for {self._attr_name}; skipping stop."
                )
            self._in_motion = False
            self._direction = None
            if self._movement_task is not None and not self._movement_task.done():
                self._movement_task.cancel()
                self.hass.async_create_task(self._wait_for_movement_task())

        else:
            _LOGGER.warning(f"Unknown state '{new_state}' for {self._attr_name}")

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        try:
            await self._start_movement("opening")
        except Exception as e:
            _LOGGER.error(f"Failed to open cover {self._attr_name}: {e}")

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        try:
            await self._start_movement("closing")
        except Exception as e:
            _LOGGER.error(f"Failed to close cover {self._attr_name}: {e}")

    async def _start_movement(self, direction, target_position=None):
        """Start movement in the specified direction."""
        if self._in_motion:
            # Stop the current movement
            await self.async_stop_cover()

        # Define completion handler
        async def completion_handler(success):
            if success:
                self._direction = direction
                self._in_motion = True
                self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
                self._position_estimator.start(self._direction, self._position)
                self.async_write_ha_state()
                await self._start_position_estimation(target_position=target_position)
            else:
                _LOGGER.error(f"Command to {direction} cover {self._attr_name} failed.")

        # Send the command to operate the cover
        await self._operate_cover(direction, completion_handler)

    async def _operate_cover(self, direction, completion_handler):
        """Send the command to operate the cover."""
        _LOGGER.debug("Operating cover %s in direction: %s", self._attr_name, direction)

        # Queue the command with the completion handler
        if direction == "opening":
            await self._coordinator.api.open_cover(
                self._address, self._channel, completion_handler=completion_handler
            )
        elif direction == "closing":
            await self._coordinator.api.close_cover(
                self._address, self._channel, completion_handler=completion_handler
            )
        else:
            _LOGGER.error(f"Invalid direction {direction} for cover {self._attr_name}")
            return

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        try:
            # Define completion handler
            async def completion_handler(success):
                if success:
                    await self._finalize_position_estimate()
                else:
                    _LOGGER.error(f"Command to stop cover {self._attr_name} failed.")

            await self._coordinator.api.stop_cover(
                self._address,
                self._channel,
                self._direction,
                completion_handler=completion_handler,
            )
        except Exception as e:
            _LOGGER.error(f"Failed to stop cover {self._attr_name}: {e}")

    async def _finalize_position_estimate(self):
        """Finalize the position estimate and stop all movement-related tasks."""
        _LOGGER.debug("Finalizing position for %s", self._attr_name)
        if self._position_estimator._start_time is not None:
            self._position_estimator.stop()
            if self._position_estimator.position is not None:
                self._position = self._position_estimator.position
            else:
                _LOGGER.warning(
                    f"Position estimator returned None for position in _finalize_position_estimate for {self._attr_name}"
                )
        else:
            _LOGGER.debug(
                f"Position estimator was not started for {self._attr_name}; skipping stop."
            )

        # Cancel the movement task if it's running
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        # Reset motion and direction states
        self._in_motion = False
        self._direction = None
        self._button_operation_time = None

        # Set the state to STATE_STOPPED
        self._state = STATE_STOPPED

        # Update the Home Assistant state
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Set the cover to a specific position."""
        target_position = kwargs.get(ATTR_POSITION)
        if target_position is None:
            return

        # Debounce logic to avoid rapid commands
        current_time = time.monotonic()
        if current_time - self._last_position_change_time < 1:
            _LOGGER.debug(
                "Skipping position update for %s due to rapid command frequency.",
                self._attr_name,
            )
            return

        self._last_position_change_time = current_time

        # If the cover is already at the target position, do nothing
        if self._position == target_position:
            _LOGGER.debug(
                "Cover %s is already at the target position.", self._attr_name
            )
            return

        # Determine direction based on the target vs. current position
        direction = "opening" if target_position > self._position else "closing"

        try:
            await self._start_movement(direction, target_position=target_position)
        except Exception as e:
            _LOGGER.error(f"Failed to set position for cover {self._attr_name}: {e}")

    async def _start_position_estimation(self, target_position=None):
        """Start position estimation and schedule the update task."""
        # Cancel and await previous movement task if it's still running
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        # Schedule the _update_position task
        self._movement_task = self.hass.async_create_task(
            self._update_position(target_position)
        )

    async def _update_position(self, target_position=None):
        """Periodically update the position of the cover during movement."""
        start_time = time.monotonic()

        try:
            while self._in_motion:
                if self._position is None:
                    _LOGGER.error(
                        "self._position is None in _update_position for %s",
                        self._attr_name,
                    )
                    # Default to a safe position
                    self._position = 0 if self._direction == "closing" else 100
                    _LOGGER.warning(
                        "Defaulting self._position to %s for %s",
                        self._position,
                        self._attr_name,
                    )

                # Update the estimated position
                estimated_position = self._position_estimator.get_position()
                if estimated_position is not None:
                    self._position = estimated_position
                else:
                    _LOGGER.warning(
                        "Position estimator returned None in _update_position for %s",
                        self._attr_name,
                    )

                # Stop if button operation time has elapsed
                elapsed = time.monotonic() - start_time
                if (
                    self._button_operation_time
                    and elapsed >= self._button_operation_time
                ):
                    await self.async_stop_cover()
                    return

                # Stop if the target position is reached
                if target_position is not None:
                    if (
                        self._direction == "opening"
                        and self._position >= target_position
                    ) or (
                        self._direction == "closing"
                        and self._position <= target_position
                    ):
                        self._position = target_position
                        self._in_motion = False
                        self._direction = None
                        self._state = STATE_STOPPED
                        self.async_write_ha_state()
                        await self.async_stop_cover()
                        return

                # Handle full open or closed state with buffer time
                if (self._direction == "opening" and self._position >= 100) or (
                    self._direction == "closing" and self._position <= 0
                ):
                    self._position = 100 if self._direction == "opening" else 0
                    self._in_motion = False
                    self._direction = None
                    self._state = STATE_STOPPED
                    self.async_write_ha_state()
                    await asyncio.sleep(FULL_OPERATION_BUFFER)
                    await self.async_stop_cover()
                    return

                # Write state and wait before the next iteration
                self.async_write_ha_state()
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            _LOGGER.debug(f"Position update for {self._attr_name} was cancelled")
