"""Cover platform for the Nikobus integration with module-level devices."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass,
    ATTR_POSITION,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"

STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
STATE_ERROR = 0x03


class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds: float, start_position: float):
        self._duration_in_seconds = duration_in_seconds
        self._start_time: float | None = None
        self._direction: int | None = None
        self.position: float | None = start_position

        _LOGGER.debug(
            "PositionEstimator initialized with duration: %s seconds",
            duration_in_seconds,
        )

    def start(self, direction: str, position: float | None = None) -> None:
        """Start the movement in the given direction."""
        if self._start_time is not None:
            _LOGGER.debug("PositionEstimator.start() called but already started.")
            return

        self._direction = 1 if direction == "opening" else -1
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

    def get_position(self) -> int | None:
        """Calculate and return the current position estimate."""
        if self._start_time is None or self._direction is None or self.position is None:
            return None

        elapsed_time = time.monotonic() - self._start_time
        progress = (elapsed_time / self._duration_in_seconds) * 100 * self._direction
        new_position = max(0, min(100, self.position + progress))
        return int(new_position)

    def stop(self) -> None:
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
    def duration_in_seconds(self) -> float:
        """Publicly expose the duration_in_seconds attribute."""
        return self._duration_in_seconds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """
    Set up Nikobus cover entities from a config entry.

    This organizes covers under:
      - A main hub device (registered in __init__.py),
      - Each roller module as a child device,
      - Each channel as a CoverEntity belonging to its module device.
    """
    _LOGGER.debug("Setting up Nikobus cover entities.")

    coordinator: NikobusDataCoordinator = hass.data[DOMAIN]["coordinator"]
    roller_modules: Dict[str, Any] = coordinator.dict_module_data.get("roller_module", {})

    device_registry = dr.async_get(hass)
    entities: List[NikobusCoverEntity] = []

    for address, cover_module_data in roller_modules.items():
        module_desc = cover_module_data.get("description", f"Roller Module {address}")
        module_model = cover_module_data.get("model", "Unknown Roller Model")

        # 1) Register each roller module as a child device of the hub
        _register_nikobus_roller_device(
            device_registry=device_registry,
            entry=entry,
            module_address=address,
            module_name=module_desc,
            module_model=module_model,
        )

        # 2) Create a cover entity for each channel
        for channel_idx, channel_info in enumerate(
            cover_module_data.get("channels", []), start=1
        ):
            if channel_info["description"].startswith("not_in_use"):
                continue

            operation_time = channel_info.get("operation_time", "30")
            entity = NikobusCoverEntity(
                hass=hass,
                coordinator=coordinator,
                address=address,
                channel=channel_idx,
                channel_description=channel_info["description"],
                module_desc=module_desc,
                module_model=module_model,
                operation_time=operation_time,
            )
            entities.append(entity)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus cover entities.", len(entities))


def _register_nikobus_roller_device(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    module_address: str,
    module_name: str,
    module_model: str,
) -> None:
    """
    Register a single Nikobus roller module as a child device in the device registry.

    Linked to the main hub device via 'nikobus_hub'.
    """
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, module_address)},  # Unique ID for this roller module
        manufacturer=BRAND,
        name=module_name,
        model=module_model,
        # Link this module device to the main hub
        via_device=(DOMAIN, HUB_IDENTIFIER),
    )


class NikobusCoverEntity(CoordinatorEntity, CoverEntity, RestoreEntity):
    """Represents a Nikobus cover entity (one channel on a roller module)."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_desc: str,
        module_model: str,
        operation_time: str,
    ) -> None:
        """
        Initialize the cover entity with data from the Nikobus system.

        Args:
            hass: Home Assistant instance.
            coordinator: NikobusDataCoordinator.
            address: Unique address for the module in Nikobus.
            channel: Channel index on the module.
            channel_description: Friendly name of the channel.
            module_desc: The module's descriptive name.
            module_model: The module's model identifier.
            operation_time: Time in seconds for full open/close.
        """
        super().__init__(coordinator)
        self.hass = hass
        self._address = address
        self._channel = channel
        self._description = module_desc
        self._model = module_model
        self._direction: int | None = None
        self._state = STATE_STOPPED
        self._position = 100  # Default to fully open
        self._previous_state: int | None = None
        self._movement_source = "ha"

        self._operation_time = float(operation_time)
        self._position_estimator = PositionEstimator(
            duration_in_seconds=self._operation_time,
            start_position=self._position,
        )

        self._in_motion = False
        self._movement_task: asyncio.Task | None = None
        self._last_position_change_time = time.monotonic()
        self._button_operation_time: float | None = None

        # Basic entity attributes
        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

        _LOGGER.debug(
            "NikobusCoverEntity initialized for %s (address=%s, channel=%s, operation_time=%s)",
            channel_description, address, channel, operation_time
        )

    @property
    def device_info(self) -> Dict[str, Any]:
        """
        Provide device information for grouping under this roller module.

        Each entity references identifiers={(DOMAIN, self._address)},
        which is the module device. The module itself references the hub
        via `via_device` when we register it in _register_nikobus_roller_device().
        """
        return {
            "identifiers": {(DOMAIN, self._address)},
            "manufacturer": BRAND,
            "name": self._description,
            "model": self._model,
        }

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        attrs = super().extra_state_attributes or {}
        attrs["position"] = self._position
        attrs["state"] = self._state
        return attrs

    @property
    def current_cover_position(self):
        """Return the current position of the cover (0..100)."""
        return self._position

    @property
    def is_open(self):
        """Return True if the cover is fully open (position=100)."""
        return self._position == 100

    @property
    def is_closed(self):
        """Return True if the cover is fully closed (position=0)."""
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
        return self._state != STATE_ERROR

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

        # Restore the previous position
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
                self._position = 100
        else:
            _LOGGER.debug(
                "No last state available for %s. Using default position 100",
                self._attr_name,
            )
            self._position = 100

        # Initialize state from current API state cached in memory
        self._state = self.coordinator.get_cover_state(self._address, self._channel)
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        new_state = self.coordinator.get_cover_state(self._address, self._channel)
        if new_state != self._previous_state:
            self.hass.async_create_task(self._process_state_change(new_state))
            self.async_write_ha_state()

    async def _handle_nikobus_button_event(self, event):
        """Handle the nikobus_button_pressed event and update cover state."""
        impacted_module_address = event.data.get("impacted_module_address")
        button_operation_time = event.data.get("button_operation_time", None)

        if impacted_module_address != self._address:
            _LOGGER.debug("Skipping event for %s (not impacted)", self._attr_name)
            return

        if button_operation_time:
            _LOGGER.debug(
                "Button operation_time received for %s: %s",
                self._attr_name,
                button_operation_time,
            )
            self._button_operation_time = float(button_operation_time)

        new_state = self.coordinator.get_cover_state(self._address, self._channel)
        if new_state != self._previous_state:
            _LOGGER.debug(
                "State changed for %s: %s -> %s",
                self._attr_name,
                self._previous_state,
                new_state
            )
            await self._process_state_change(new_state, source="nikobus")
            self.async_write_ha_state()
        else:
            _LOGGER.debug("No state change for %s, ignoring event.", self._attr_name)

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        try:
            await self._start_movement("opening")
        except Exception as exc:
            _LOGGER.error("Failed to open cover %s: %s", self._attr_name, exc)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        try:
            await self._start_movement("closing")
        except Exception as exc:
            _LOGGER.error("Failed to close cover %s: %s", self._attr_name, exc)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        try:
            # Define a completion handler
            async def completion_handler():
                await self._finalize_movement()

            await self.coordinator.api.stop_cover(
                self._address,
                self._channel,
                self._direction,
                completion_handler=completion_handler,
            )
        except Exception as exc:
            _LOGGER.error("Failed to stop cover %s: %s", self._attr_name, exc)

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
            _LOGGER.debug("Cover %s is already at target position.", self._attr_name)
            return

        # Determine direction based on target vs. current
        direction = "opening" if target_position > self._position else "closing"

        try:
            await self._start_movement(direction, target_position=target_position)
        except Exception as exc:
            _LOGGER.error(
                "Failed to set position for cover %s: %s", self._attr_name, exc
            )

    async def _process_state_change(self, new_state, source="ha"):
        """Process a state change from the coordinator or a button event."""
        _LOGGER.debug(
            "State changed from %s to %s for %s",
            self._previous_state,
            new_state,
            self._attr_name,
        )

        # If already at final position, no need to proceed
        if (new_state == STATE_OPENING and self._position == 100) or (
            new_state == STATE_CLOSING and self._position == 0
        ):
            _LOGGER.debug(
                "Cover %s is already at intended position %d. No state change.",
                self._attr_name,
                self._position,
            )
            self.coordinator.set_bytearray_state(self._address, self._channel, 0x00)
            return

        self._previous_state = new_state
        self._movement_source = source

        if new_state in (STATE_OPENING, STATE_CLOSING):
            if self._in_motion:
                if self._state == new_state:
                    return
                await self._finalize_movement()

            self._direction = "opening" if new_state == STATE_OPENING else "closing"
            self._in_motion = True
            self._state = new_state
            self._position_estimator.start(self._direction, self._position)
            self._movement_task = self.hass.async_create_task(self._update_position())

        elif new_state == STATE_STOPPED:
            if self._in_motion:
                await self._finalize_movement()

        elif new_state == STATE_ERROR:
            await self.async_stop_cover()
            _LOGGER.warning("Error state (0x03) encountered for %s.", self._attr_name)
        else:
            _LOGGER.warning("Unknown state '%s' for %s", new_state, self._attr_name)

    async def _start_movement(self, direction, target_position=None):
        """Start movement in the specified direction."""
        if self._in_motion:
            await self._finalize_movement()

        async def completion_handler():
            self._direction = direction
            self._in_motion = True
            self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
            self._position_estimator.start(self._direction, self._position)
            await self._start_position_estimation(target_position=target_position)
            self.async_write_ha_state()

        await self._operate_cover(direction, completion_handler)

    async def _operate_cover(self, direction, completion_handler):
        """Send the command to operate the cover."""
        _LOGGER.debug("Operating cover %s in direction: %s", self._attr_name, direction)

        if direction == "opening":
            await self.coordinator.api.open_cover(
                self._address, self._channel, completion_handler=completion_handler
            )
        elif direction == "closing":
            await self.coordinator.api.close_cover(
                self._address, self._channel, completion_handler=completion_handler
            )
        else:
            _LOGGER.error("Invalid direction %s for cover %s", direction, self._attr_name)

    async def _start_position_estimation(self, target_position=None):
        """Start position estimation and schedule the update task."""
        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

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
                    self._position = 0 if self._direction == "closing" else 100
                    _LOGGER.warning(
                        "Defaulting self._position to %s for %s",
                        self._position,
                        self._attr_name,
                    )

                estimated_position = self._position_estimator.get_position()
                if estimated_position is not None:
                    self._position = estimated_position
                else:
                    _LOGGER.warning(
                        "Position estimator returned None in _update_position for %s",
                        self._attr_name,
                    )

                # Check button operation time
                elapsed = time.monotonic() - start_time
                if self._button_operation_time and elapsed >= self._button_operation_time:
                    await self.async_stop_cover()
                    return

                # Stop if the target position is reached
                if target_position is not None:
                    if (
                        self._direction == "opening"
                        and self._position >= target_position
                        and target_position < 100
                    ) or (
                        self._direction == "closing"
                        and self._position <= target_position
                        and target_position > 0
                    ):
                        _LOGGER.debug("Position reached for %s", self._position)
                        self._position = target_position
                        self._in_motion = False
                        self._direction = None
                        self._state = STATE_STOPPED
                        if self._movement_source == "ha":
                            await self.async_stop_cover()
                        else:
                            await self._finalize_movement()
                        return

                # If fully open/close
                if (self._direction == "opening" and self._position >= 100) or (
                    self._direction == "closing" and self._position <= 0
                ):
                    _LOGGER.debug("Full Position reached for %s", self._position)
                    self._position = 100 if self._direction == "opening" else 0
                    self._in_motion = False
                    self._direction = None
                    self._state = STATE_STOPPED
                    self.async_write_ha_state()
                    if self._movement_source == "ha":
                        await asyncio.sleep(self._operation_time)
                        await self.async_stop_cover()
                    else:
                        await self._finalize_movement()
                    return

                self.async_write_ha_state()
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            _LOGGER.debug("Position update for %s was cancelled", self._attr_name)

    async def _finalize_movement(self):
        """Finalize cover movement, stop the estimator, cancel tasks, etc."""
        _LOGGER.debug("Finalizing movement for %s", self._attr_name)

        if self._position_estimator._start_time is not None:
            self._position_estimator.stop()
            if self._position_estimator.position is not None:
                self._position = self._position_estimator.position
            else:
                _LOGGER.warning(
                    "Position estimator returned None when finalizing for %s",
                    self._attr_name,
                )

        if self._movement_task is not None and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Movement task for %s was cancelled.", self._attr_name)

        self._in_motion = False
        self._direction = None
        self._button_operation_time = None
        self._state = STATE_STOPPED

        # Immediately set the new state in memory
        self.coordinator.set_bytearray_state(self._address, self._channel, 0x00)

        self.async_write_ha_state()
