"""Cover platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
# Note: If ConfigEntry has been moved in your HA version, ensure this import is correct
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    BRAND,
    DEFAULT_COVER_ASSUMED_STATE,
    DEFAULT_COVER_DEBOUNCE_DELAY,
    DEFAULT_COVER_MOVEMENT_BUFFER,
    DEFAULT_COVER_OPERATION_TIME,
    DOMAIN,
)
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity
from .nkbtravelcalculator import NikobusTravelCalculator
from .router import build_unique_id, get_routing

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"


def _parse_operation_time(value: Any, fallback: float, label: str, address: str) -> float:
    """Parse and validate a cover operation time value.

    ``None`` means the field was not configured — silently returns ``fallback``.
    Any other value that is not a positive number logs a warning before falling back.
    """
    if value is None:
        return fallback
    try:
        t = float(value)
        if t > 0:
            return t
    except (TypeError, ValueError):
        pass
    _LOGGER.warning(
        "Cover %s: invalid %s %r — must be a positive number. Using default %.1fs.",
        address,
        label,
        value,
        fallback,
    )
    return fallback

# Nikobus Internal States
STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
STATE_ERROR = 0x03  # Catches logic engine conflicts

# Event Constants
EVENT_BUTTON_OPERATION = "nikobus_button_operation"
EVENT_BUTTON_PRESS = "nikobus_button_pressed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nikobus cover entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    routing = get_routing(hass, entry, coordinator.dict_module_data)

    # Use hardcoded defaults
    assumed_state_config = DEFAULT_COVER_ASSUMED_STATE
    movement_buffer_config = DEFAULT_COVER_MOVEMENT_BUFFER
    debounce_delay_config = DEFAULT_COVER_DEBOUNCE_DELAY

    entities = []
    for spec in routing["cover"]:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, spec.address)},
            manufacturer=BRAND,
            name=spec.module_desc,
            model=spec.module_model,
            via_device=(DOMAIN, HUB_IDENTIFIER),
        )

        op_time_up = _parse_operation_time(
            spec.operation_time_up,
            DEFAULT_COVER_OPERATION_TIME,
            "operation_time_up",
            spec.address,
        )
        op_time_down = _parse_operation_time(
            spec.operation_time_down,
            op_time_up,
            "operation_time_down",
            spec.address,
        )

        entities.append(
            NikobusCoverEntity(
                coordinator,
                spec.address,
                spec.channel,
                spec.channel_description,
                spec.module_desc,
                spec.module_model,
                op_time_up,
                op_time_down,
                assumed_state_config,
                movement_buffer_config,
                debounce_delay_config,
            )
        )

    async_add_entities(entities)


class NikobusCoverEntity(NikobusEntity, CoverEntity, RestoreEntity):
    """Representation of a Nikobus cover entity."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        description: str,
        module_desc: str,
        model: str,
        op_time_up: float,
        op_time_down: float,
        assumed_state_config: bool,
        movement_buffer: float,
        debounce_delay: float,
    ) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator, address, module_desc, model)
        self._address = address
        self._channel = channel
        self._channel_description = description

        self._attr_name = description
        self._attr_unique_id = build_unique_id("cover", "cover", address, channel)

        self._attr_assumed_state = assumed_state_config
        self._movement_buffer = movement_buffer
        self._debounce_delay = debounce_delay

        # Initialize calculator with directional specific times
        self._calculator = NikobusTravelCalculator(op_time_up, op_time_down)

        self._position: float = 100.0
        self._state = STATE_STOPPED
        self._target_position: int | None = None
        self._motion_task: asyncio.Task | None = None
        self._coalesce_task: asyncio.Task | None = None

        self._movement_source = "ha"
        self._current_run_limit: float = op_time_up

    @property
    def current_cover_position(self) -> int:
        """Return the current position of the cover (0-100)."""
        return int(round(self._position))

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._state == STATE_OPENING and self._position < 100

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._state == STATE_CLOSING and self._position > 0

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed (0)."""
        return self.current_cover_position == 0

    @property
    def is_open(self) -> bool:
        """Return if the cover is open (100)."""
        return self.current_cover_position == 100
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, merging with parent attributes."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "operation_time_up": self._calculator.time_up,
            "operation_time_down": self._calculator.time_down,
            "movement_buffer": self._movement_buffer,
            "movement_source": self._movement_source,
        }

    async def async_added_to_hass(self) -> None:
        """Restore state and listen for Nikobus bus events."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            if (pos := last_state.attributes.get(ATTR_CURRENT_POSITION)) is not None:
                self._position = float(pos)
                self._calculator.set_position(self._position)

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_OPERATION, self._handle_nikobus_event)
        )
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESS, self._handle_button_pressed)
        )

        def _cancel_cover_tasks() -> None:
            if self._motion_task:
                self._motion_task.cancel()
                self._motion_task = None
            if self._coalesce_task:
                self._coalesce_task.cancel()
                self._coalesce_task = None

        self.async_on_remove(_cancel_cover_tasks)

    @callback
    def _handle_coordinator_update(self) -> None:
        """React to state changes on the Nikobus bus."""
        new_bus_state = self.coordinator.get_cover_state(self._address, self._channel)

        if new_bus_state == self._state:
            super()._handle_coordinator_update()
            return

        # 0x03 = hardware motor-protection state: both open and close outputs
        # briefly active simultaneously as a braking mechanism. The module
        # auto-clears to VALUE=0; HA must not send any command in response.
        # Just freeze tracking — the next coordinator refresh will confirm 0x00.
        if new_bus_state == STATE_ERROR:
            _LOGGER.debug("Cover %s: hardware motor-protection (0x03) observed — waiting for auto-clear.", self._address)
            self.hass.async_create_task(self._stop(send_stop=False))
        elif new_bus_state == STATE_STOPPED:
            self.hass.async_create_task(self._stop(send_stop=False))
        else:
            direction = "opening" if new_bus_state == STATE_OPENING else "closing"
            self._movement_source = "nikobus"
            self._start_motion_logic(direction)

        super()._handle_coordinator_update()

    async def _handle_button_pressed(self, event: Any) -> None:
        """Cancel HA motion tracking when a linked Nikobus button is pressed.

        When the user presses a physical Nikobus button during movement, HA must
        not send any command back to the bus. Nikobus handles the stop internally
        (motor-protection: activates the opposing direction momentarily to brake,
        then auto-clears to VALUE=0). HA's role is observer only.

        Cancel the motion task so position tracking stops and HA state is written
        as STOPPED. The actuator's Step-1 GET (~300 ms later) will confirm the
        actual hardware state and _handle_coordinator_update will resync if needed.
        """
        if str(event.data.get("module_address")) != str(self._address):
            return

        if self._state != STATE_STOPPED:
            await self._stop(send_stop=False)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover command."""
        await self._request_move("opening")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover command."""
        await self._request_move("closing")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop cover command."""
        await self._stop(send_stop=True)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move cover to a specific percentage."""
        target = kwargs[ATTR_POSITION]
        if self._coalesce_task:
            self._coalesce_task.cancel()

        async def _debounced_move() -> None:
            await asyncio.sleep(self._debounce_delay)
            direction = "opening" if target > self._position else "closing"
            await self._request_move(direction, target)

        self._coalesce_task = self.hass.async_create_task(_debounced_move())

    async def _request_move(self, direction: str, target: int | None = None) -> None:
        """Execute movement via the API."""
        
        # Motor protection & Nikobus logic sync: Stop before reversing
        if self._state != STATE_STOPPED:
            current_direction = "opening" if self._state == STATE_OPENING else "closing"
            if current_direction != direction:
                await self._stop(send_stop=True)
                await asyncio.sleep(0.5) # Give the motor and bus time to settle

        self._movement_source = "ha"
        self._target_position = target

        async def on_sent() -> None:
            self._start_motion_logic(direction)

        if direction == "opening":
            await self.coordinator.api.open_cover(self._address, self._channel, on_sent)
        else:
            await self.coordinator.api.close_cover(self._address, self._channel, on_sent)

    def _start_motion_logic(self, direction: str, limit_time: float | None = None) -> None:
        """Initialize the virtual travel tracker."""
        if self._motion_task:
            self._motion_task.cancel()

        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
        self._calculator.start_travel(direction)

        # Select travel time based on direction
        active_op_time = (
            self._calculator.time_up if direction == "opening" else self._calculator.time_down
        )
        
        # Limit is travel time + movement buffer (usually 3s)
        self._current_run_limit = float(limit_time) if limit_time else (active_op_time + self._movement_buffer)

        self._motion_task = self.hass.async_create_task(self._motion_loop())
        self.async_write_ha_state()

    async def _motion_loop(self) -> None:
        """Update position periodically while moving."""
        try:
            start_time = time.monotonic()
            while self._state in (STATE_OPENING, STATE_CLOSING):
                elapsed = time.monotonic() - start_time
                self._position = self._calculator.current_position()

                if elapsed >= self._current_run_limit or self._should_stop():
                    # Stop logic: send stop command only if initiated by HA
                    await self._stop(send_stop=(self._movement_source == "ha"))
                    break

                self.async_write_ha_state()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    def _should_stop(self) -> bool:
        """Check if cover reached the target position."""
        if self._state == STATE_OPENING:
            return self._target_position is not None and self._position >= self._target_position
        if self._state == STATE_CLOSING:
            return self._target_position is not None and self._position <= self._target_position
        return False

    async def _stop(self, send_stop: bool = False, force_api: bool = False) -> None:
        """Stop movement and finalize position."""
        if self._motion_task:
            self._motion_task.cancel()
            self._motion_task = None

        self._calculator.stop()
        self._position = self._calculator.current_position()

        if send_stop and (self._state != STATE_STOPPED or force_api):
            dir_cmd = "opening" if self._state == STATE_OPENING else "closing"
            await self.coordinator.api.stop_cover(
                self._address, self._channel, dir_cmd, lambda: None
            )

        self._state = STATE_STOPPED
        self._target_position = None
        self.coordinator.set_bytearray_state(self._address, self._channel, STATE_STOPPED)
        self.async_write_ha_state()

    async def _handle_nikobus_event(self, event: Any) -> None:
        """Handle physical button press feedback from the bus."""
        if str(event.data.get("impacted_module_address")) != str(self._address):
            return

        new_bus_state = self.coordinator.get_cover_state(self._address, self._channel)
        
        if new_bus_state == self._state:
            return

        if new_bus_state in (STATE_OPENING, STATE_CLOSING):
            self._movement_source = "nikobus"
            direction = "opening" if new_bus_state == STATE_OPENING else "closing"
            self._start_motion_logic(direction, event.data.get("button_operation_time"))
        elif new_bus_state == STATE_STOPPED:
            await self._stop(send_stop=False)
