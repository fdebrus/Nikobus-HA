"""Cover platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CATEGORY_CENTRAL_FUNCTIONS,
    DEFAULT_COVER_DEBOUNCE_DELAY,
    DEFAULT_COVER_MOVEMENT_BUFFER,
    DEFAULT_COVER_OPERATION_TIME,
    DOMAIN,
    press_signal,
)
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity, command_error
from .nkbreconcile import cf_cover_members, is_pure_roller_cf
from .nkbtravelcalculator import NikobusTravelCalculator
from .router import build_unique_id, get_routing, register_output_module_devices

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


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


def _parse_cf_time(value: Any) -> float | None:
    """Parse a CF member timing string (e.g. ``"30 s"`` / ``"2 m"``) to seconds.

    Returns ``None`` when the value is missing or unparseable, so the caller
    can fall back to the module's configured operation time.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    minutes = text.endswith("m") and not text.endswith("ms")
    number = text.rstrip("ms ").strip()
    try:
        seconds = float(number)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return seconds * 60.0 if minutes else seconds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus cover entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    routing = get_routing(hass, entry, coordinator.dict_module_data)
    specs = routing.get("cover", [])
    register_output_module_devices(hass, entry, specs)

    entities = []
    for spec in specs:
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
            )
        )

    # Pure-roller central functions (every member a shutter channel) become
    # actionable grouped covers. A roller CF bundles its members' open /
    # close / toggle links, so broadcasting its address can't express a
    # direction; instead the grouped cover drives the member channels
    # through the atomic per-module commit path (like a scene). This works
    # for ``M01`` toggle groups too, because it commands the output state
    # directly rather than replaying a link.
    cf_storage = getattr(coordinator, "cf_storage", None)
    cf_data = cf_storage.data.get("nikobus_cf", {}) if cf_storage is not None else {}
    if isinstance(cf_data, dict):
        for bus_address, cf in cf_data.items():
            if not isinstance(cf, dict) or not is_pure_roller_cf(cf):
                continue
            members = cf_cover_members(cf)
            if not members:
                continue
            entities.append(
                NikobusCFCoverEntity(coordinator, str(bus_address), cf, members)
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
    ) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator, address, module_desc, model)
        self._address = address
        self._channel = channel
        self._channel_description = description

        self._attr_name = description
        self._attr_unique_id = build_unique_id("cover", "cover", address, channel)

        self._calculator = NikobusTravelCalculator(op_time_up, op_time_down)

        self._position: float = 100.0
        self._state = STATE_STOPPED
        self._target_position: int | None = None
        self._motion_task: asyncio.Task[None] | None = None
        self._coalesce_task: asyncio.Task[None] | None = None
        self._error_recovery_task: asyncio.Task[None] | None = None

        self._movement_source = "ha"
        self._current_run_limit: float = 0.0
        # Last direction a motion actually ran in — used for the stop
        # command when _state has already been committed to STOPPED
        # (e.g. the 0x03 motor-protection clear, where the old code
        # always fell through to "closing").
        self._last_motion_direction = "closing"
        # One protection-clear write per 0x03 episode; re-armed when the
        # bus leaves the error state.
        self._error_clear_sent = False

        # Set by _handle_button_pressed when a physical button starts a new move.
        # Used by _handle_coordinator_update to backdate the travel calculator so
        # that the in-transit position reflects actual elapsed travel time.
        self._last_button_press_monotonic: float | None = None

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

    def _render_state(self) -> Any:
        """Diff on bus state + rounded position so an idle, unchanged poll
        skips the write. Position updates during motion are written
        directly by the motion loop and so bypass this entirely."""
        return (self._state, self.current_cover_position)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, merging with parent attributes."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "operation_time_up": self._calculator.time_up,
            "operation_time_down": self._calculator.time_down,
            "movement_source": self._movement_source,
            "controlled_by": self.coordinator.get_controlled_by(self._address, self._channel),
        }

    async def async_added_to_hass(self) -> None:
        """Restore state and listen for Nikobus bus events."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            if (pos := last_state.attributes.get(ATTR_CURRENT_POSITION)) is not None:
                self._position = float(pos)
                self._calculator.set_position(self._position)

        # Per-address signal keyed by this cover's module address: only
        # this module's covers are woken on a press, instead of a global
        # EVENT_BUTTON_PRESSED listener every cover runs. Channel is still
        # filtered below (one module carries several covers).
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, press_signal(self._address), self._handle_button_pressed
            )
        )

        def _cancel_cover_tasks() -> None:
            for task_attr in ("_motion_task", "_coalesce_task", "_error_recovery_task"):
                task = getattr(self, task_attr, None)
                if task:
                    task.cancel()
                    setattr(self, task_attr, None)

        self.async_on_remove(_cancel_cover_tasks)

    @callback
    def _handle_coordinator_update(self) -> None:
        """React to state changes reported by the Nikobus bus."""
        new_bus_state = self.coordinator.get_cover_state(self._address, self._channel)

        # Re-arm the one-shot protection clear as soon as the bus has
        # left 0x03 — before the same-state early return below, which
        # would otherwise skip it (STOPPED == STOPPED).
        if new_bus_state != STATE_ERROR:
            self._error_clear_sent = False

        if new_bus_state == self._state:
            super()._handle_coordinator_update()
            return

        # 0x03 = hardware motor-protection (both relay outputs active simultaneously).
        # Cause depends on who initiated movement:
        #   "ha"      — HA wrote the relay directly; Nikobus didn't track the motion.
        #               Actively send VALUE=0 to clear the braking state quickly.
        #   "nikobus" — Nikobus initiated; hardware auto-clears cleanly. Observer-only;
        #               schedule a follow-up refresh to catch the 0x00 transition.
        if new_bus_state == STATE_ERROR:
            if self._movement_source == "ha":
                if not self._error_clear_sent:
                    self._error_clear_sent = True
                    _LOGGER.debug(
                        "Cover %s ch%d motor-protection (0x03) after HA move — writing 0 to clear",
                        self._address, self._channel,
                    )
                    self.hass.async_create_task(
                        self._stop(send_stop=True, force_api=True)
                    )
            else:
                _LOGGER.debug(
                    "Cover %s ch%d motor-protection (0x03) after Nikobus move — waiting for auto-clear",
                    self._address, self._channel,
                )
                self.hass.async_create_task(self._stop(send_stop=False))
                if not self._error_recovery_task or self._error_recovery_task.done():
                    async def _await_error_clear() -> None:
                        await asyncio.sleep(2.5)
                        await self.coordinator.async_request_refresh()
                    self._error_recovery_task = self.hass.async_create_task(_await_error_clear())

        elif new_bus_state == STATE_STOPPED:
            self.hass.async_create_task(self._stop(send_stop=False))

        else:
            direction = "opening" if new_bus_state == STATE_OPENING else "closing"
            self._movement_source = "nikobus"

            # Calculate how long the cover has already been moving before HA
            # detected the state change.  The timestamp is set by
            # _handle_button_pressed when the physical button fires — it
            # captures the actual press time, not the detection time.
            detection_latency = 0.0
            if self._last_button_press_monotonic is not None:
                age = time.monotonic() - self._last_button_press_monotonic
                if age < 10.0:
                    detection_latency = age
                self._last_button_press_monotonic = None

            self._start_motion_logic(direction, detection_latency=detection_latency)

        super()._handle_coordinator_update()

    async def _handle_button_pressed(self, data: dict[str, Any]) -> None:
        """Handle a physical Nikobus button press event (routed by module).

        Records the press timestamp (for detection-latency calculation) only
        when the cover is currently stopped — meaning this press is starting a
        new move.  Presses while moving are stop commands; we don't timestamp
        those because they are not the start of a new Nikobus-initiated move.

        Channel filtering prevents covers on the same roller module from
        sharing timestamps when unrelated buttons are pressed.
        """
        # Only process events for this specific channel to avoid cross-channel
        # timestamp pollution on multi-channel roller modules.
        event_channel = data.get("channel")
        if event_channel is not None and int(event_channel) != self._channel:
            return

        if self._state == STATE_STOPPED:
            # Cover is about to start moving — record now for detection latency.
            # A None channel (undecoded button link) is acceptable here: the
            # timestamp is benign (bounded to 10 s, overwritten on use).
            self._last_button_press_monotonic = time.monotonic()
        elif event_channel is not None:
            # Cover is moving — this press is a stop command. Require a
            # RESOLVED channel: with channel=None (undecoded link) every
            # cover on the module receives this event, and stopping a
            # moving cover on an unrelated button press would desync the
            # simulated position from the physical shutter.
            send_stop = (self._movement_source == "ha")
            await self._stop(send_stop=send_stop)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover command."""
        try:
            await self._request_move("opening")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise command_error(err) from err

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover command."""
        try:
            await self._request_move("closing")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise command_error(err) from err

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop cover command."""
        try:
            await self._stop(send_stop=True)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise command_error(err) from err

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move cover to a specific percentage."""
        target = kwargs[ATTR_POSITION]
        if target == round(self._position):
            return
        if self._coalesce_task:
            self._coalesce_task.cancel()

        async def _debounced_move() -> None:
            try:
                await asyncio.sleep(DEFAULT_COVER_DEBOUNCE_DELAY)
                direction = "opening" if target > self._position else "closing"
                await self._request_move(direction, target)
            except asyncio.CancelledError:
                pass
            finally:
                self._coalesce_task = None

        self._coalesce_task = self.hass.async_create_task(_debounced_move())

    async def _request_move(self, direction: str, target: int | None = None) -> None:
        """Execute movement via the API."""
        # Stop before reversing to protect the motor and sync Nikobus state.
        if self._state != STATE_STOPPED:
            current_direction = "opening" if self._state == STATE_OPENING else "closing"
            if current_direction != direction:
                await self._stop(send_stop=True)
                await asyncio.sleep(0.5)

        self._movement_source = "ha"
        self._target_position = target

        async def on_sent() -> None:
            self._start_motion_logic(direction)

        if direction == "opening":
            await self.coordinator.api.open_cover(self._address, self._channel, on_sent)
        else:
            await self.coordinator.api.close_cover(self._address, self._channel, on_sent)

    def _start_motion_logic(
        self,
        direction: str,
        detection_latency: float = 0.0,
    ) -> None:
        """Initialize the virtual travel tracker."""
        if self._motion_task:
            self._motion_task.cancel()

        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
        self._last_motion_direction = direction

        active_op_time = (
            self._calculator.time_up if direction == "opening" else self._calculator.time_down
        )

        # Cap latency so a stale timestamp can't skip more than 90% of travel.
        detection_latency = min(detection_latency, active_op_time * 0.9)

        self._calculator.start_travel(direction, latency=detection_latency)
        # Sync position immediately so the UI reflects in-transit state.
        self._position = self._calculator.current_position()

        # Full-travel run limit, shortened by already-elapsed detection time.
        self._current_run_limit = max(
            DEFAULT_COVER_MOVEMENT_BUFFER,
            active_op_time - detection_latency + DEFAULT_COVER_MOVEMENT_BUFFER,
        )

        self._motion_task = self.hass.async_create_task(self._motion_loop())
        self.async_write_ha_state()

    async def _motion_loop(self) -> None:
        """Update position periodically while moving."""
        try:
            start_time = time.monotonic()
            # Capture source at loop start; _movement_source must not change
            # the stop decision mid-iteration after an await.
            movement_source = self._movement_source
            while self._state in (STATE_OPENING, STATE_CLOSING):
                elapsed = time.monotonic() - start_time
                self._position = self._calculator.current_position()

                if elapsed >= self._current_run_limit or self._should_stop():
                    if elapsed >= self._current_run_limit and self._target_position is None:
                        # Reached mechanical end-stop — snap to exact value.
                        self._position = 100.0 if self._state == STATE_OPENING else 0.0
                        self._calculator.set_position(self._position)
                    elif self._target_position is not None:
                        # Snap to exact target to eliminate 0.5 s tick overshoot.
                        self._position = float(self._target_position)
                        self._calculator.set_position(self._position)
                    await self._stop(send_stop=(movement_source == "ha"))
                    break

                self.async_write_ha_state()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            _LOGGER.error(
                "Cover %s ch%d motion loop failed — forcing stop: %s",
                self._address,
                self._channel,
                err,
                exc_info=True,
            )
            await self._stop(send_stop=False)

    def _should_stop(self) -> bool:
        """Check if cover reached the target position."""
        if self._state == STATE_OPENING:
            return self._target_position is not None and self._position >= self._target_position
        if self._state == STATE_CLOSING:
            return self._target_position is not None and self._position <= self._target_position
        return False

    async def _stop(self, send_stop: bool = False, force_api: bool = False) -> None:
        """Stop movement and finalize position."""
        task = self._motion_task
        self._motion_task = None
        if task is not None and task is not asyncio.current_task():
            # Never cancel the task we are running IN: when the motion
            # loop itself calls _stop (target reached / end-stop), a
            # self-cancel would inject CancelledError at the next await
            # — which is the api.stop_cover() call below — and the bus
            # STOP frame would silently never be sent (the loop's
            # ``except CancelledError: pass`` swallows it). The loop
            # ``break``s right after this returns, so not cancelling it
            # is correct.
            task.cancel()

        stopped_state = self._state
        self._calculator.stop()
        self._position = self._calculator.current_position()

        # Commit STATE_STOPPED immediately so any coordinator events that fire
        # during the async stop-command round-trip see a consistent stopped state,
        # preventing spurious _start_motion_logic calls from stale bus reads.
        self._state = STATE_STOPPED
        self._target_position = None
        self.coordinator.set_bytearray_state(self._address, self._channel, STATE_STOPPED)

        if send_stop and (stopped_state != STATE_STOPPED or force_api):
            if stopped_state == STATE_OPENING:
                dir_cmd = "opening"
            elif stopped_state == STATE_CLOSING:
                dir_cmd = "closing"
            else:
                # Already committed to STOPPED (force_api path, e.g. the
                # 0x03 protection clear) — use the direction the motion
                # actually ran in instead of an arbitrary "closing".
                dir_cmd = self._last_motion_direction
            await self.coordinator.api.stop_cover(self._address, self._channel, dir_cmd)

        self.async_write_ha_state()


class NikobusCFCoverEntity(NikobusEntity, CoverEntity):
    """A pure-roller central function surfaced as a grouped cover.

    A pure-roller CF bundles the roller link records for several channels —
    the keys of a physical wall control (a 2-button open/close, or an
    ``M01`` "open-stop-close" toggle). Broadcasting the CF address can't
    express a single direction, so this entity drives the member channels
    directly through the **atomic per-module commit path**
    (``set_output_states_for_module``): every channel of one module moves in
    a single bus frame, with a timed stop scheduled from the channels'
    operation times — the same engine :class:`NikobusSceneEntity` uses for
    roller scenes. The result behaves like the native Nikobus scene
    (all-at-once), while giving HA proper open / close / stop. Driving the
    output state directly makes it deterministic even for an ``M01`` toggle.

    Position is modelled at the group level by one travel calculator (seeded
    with the slowest member's run time) so "fully open/closed" reflects the
    last shutter to finish. The motion loop only advances the displayed
    position; the real bus stop is sent by the per-module timed-stop tasks,
    so the loop never cancels itself mid-command.
    """

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        bus_address: str,
        cf_config: dict[str, Any],
        members: list[dict[str, Any]],
    ) -> None:
        """Initialize the grouped CF cover from its resolved members."""
        addr = str(bus_address).upper()
        pattern = str(cf_config.get("pattern") or "roller")

        imported = cf_config.get("name")
        if isinstance(imported, str) and imported.strip():
            name = imported.strip()
        else:
            name = f"Nikobus CF cover {addr}"

        super().__init__(
            coordinator=coordinator,
            address=addr,
            name=name,
            model=f"CF Cover ({pattern})",
            via_device=(DOMAIN, CATEGORY_CENTRAL_FUNCTIONS),
            # Own device per CF, keyed off the CF (not the bare bus address),
            # so a cover whose address is also a physical button isn't merged
            # into — and renamed after — that button's device.
            device_identifier=f"cf_cover_{addr.lower()}",
        )
        self._bus_address = addr
        self._pattern = pattern
        self._attr_name = name
        self._attr_unique_id = f"nikobus_cf_cover_{addr.lower()}"

        # Resolve per-member run times: the CF's own t1 wins, else the
        # module's configured operation time for that channel/direction.
        self._members: list[dict[str, Any]] = []
        max_open = 0.0
        max_close = 0.0
        for member in members:
            module = member["module_address"]
            channel = member["channel"]
            open_t = _parse_cf_time(member.get("open_time")) or (
                coordinator.get_cover_operation_time(module, channel, "up")
            )
            close_t = _parse_cf_time(member.get("close_time")) or (
                coordinator.get_cover_operation_time(module, channel, "down")
            )
            self._members.append(
                {
                    "module_address": module,
                    "channel": channel,
                    "open_time": open_t,
                    "close_time": close_t,
                }
            )
            max_open = max(max_open, open_t)
            max_close = max(max_close, close_t)

        self._calculator = NikobusTravelCalculator(
            max_open or DEFAULT_COVER_OPERATION_TIME,
            max_close or DEFAULT_COVER_OPERATION_TIME,
        )
        self._position: float = 100.0
        self._state = STATE_STOPPED
        self._run_limit: float = 0.0
        self._motion_task: asyncio.Task[None] | None = None
        # One stop token per module, re-minted on every move so a stale
        # timed stop from a previous activation can't fire on a fresh move.
        self._module_tokens: dict[str, str] = {}
        self._stop_tasks: list[asyncio.Task[None]] = []

    @property
    def current_cover_position(self) -> int:
        """Return the current group position (0-100)."""
        return int(round(self._position))

    @property
    def is_opening(self) -> bool:
        return self._state == STATE_OPENING and self._position < 100

    @property
    def is_closing(self) -> bool:
        return self._state == STATE_CLOSING and self._position > 0

    @property
    def is_closed(self) -> bool:
        return self.current_cover_position == 0

    @property
    def is_open(self) -> bool:
        return self.current_cover_position == 100

    def _render_state(self) -> Any:
        """Group cover has no single bus channel to poll — opt out of diffing."""
        return (self._state, self.current_cover_position)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parent = super().extra_state_attributes or {}
        return {
            **parent,
            "bus_address": self._bus_address,
            "pattern": self._pattern,
            "member_count": len(self._members),
            "members": [
                {
                    "module": self.coordinator.address_label(m["module_address"]),
                    "channel": m["channel"],
                    "open_time": m["open_time"],
                    "close_time": m["close_time"],
                }
                for m in self._members
            ],
        }

    async def async_added_to_hass(self) -> None:
        """Cancel any pending motion / timed-stop tasks on removal."""
        await super().async_added_to_hass()

        def _cancel_tasks() -> None:
            self._module_tokens.clear()
            if self._motion_task:
                self._motion_task.cancel()
                self._motion_task = None
            for task in self._stop_tasks:
                task.cancel()
            self._stop_tasks.clear()

        self.async_on_remove(_cancel_tasks)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open every member shutter."""
        try:
            await self._move("opening")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise command_error(err) from err

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close every member shutter."""
        try:
            await self._move("closing")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise command_error(err) from err

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop every member shutter."""
        try:
            await self._stop()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise command_error(err) from err

    def _members_by_module(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for member in self._members:
            grouped.setdefault(member["module_address"], []).append(member)
        return grouped

    async def _move(self, direction: str) -> None:
        """Drive all members in ``direction`` via per-module atomic commits."""
        byte_val = STATE_OPENING if direction == "opening" else STATE_CLOSING
        time_key = "open_time" if direction == "opening" else "close_time"

        # Cancel any in-flight motion / pending stops from a previous move.
        self._cancel_motion()
        self._cancel_stops()

        # Longest member run time decides the shared stop deadline, mirroring
        # the scene path: the most generous buffer absorbs bus contention so a
        # slow shutter still reaches its end-stop before the stop frame fires.
        delay = 0.0
        commanded: dict[str, set[int]] = {}
        sent_states: dict[str, bytearray] = {}

        for module_id, members in self._members_by_module().items():
            current = self.coordinator.nikobus_module_states.get(module_id, bytearray(12))
            state = bytearray(current)
            for member in members:
                idx = member["channel"] - 1
                if 0 <= idx < len(state):
                    state[idx] = byte_val
                    commanded.setdefault(module_id, set()).add(idx)
                op_time = float(member[time_key] or 0.0)
                if op_time > 0:
                    delay = max(delay, op_time + DEFAULT_COVER_MOVEMENT_BUFFER)
            sent_states[module_id] = state
            await self._commit_module(module_id, state)

        # Schedule the timed stop per module once all are moving.
        for module_id, indexes in commanded.items():
            token = uuid.uuid4().hex
            self._module_tokens[module_id] = token
            task = self.hass.async_create_task(
                self._delayed_stop(module_id, sent_states[module_id], indexes, delay, token)
            )
            self._stop_tasks.append(task)
            task.add_done_callback(
                lambda t: self._stop_tasks.remove(t) if t in self._stop_tasks else None
            )

        self._start_motion(direction)

    async def _commit_module(self, module_id: str, state: bytearray) -> None:
        """Stage and push a whole module's state in one bus commit (per group)."""
        num_chans = self.coordinator.get_module_channel_count(module_id)
        self.coordinator.set_bytearray_group_state(module_id, 1, state[:6].hex())
        if num_chans > 6:
            self.coordinator.set_bytearray_group_state(module_id, 2, state[6:12].hex())
        await self.coordinator.api.set_output_states_for_module(address=module_id)
        await self.coordinator.async_event_handler(
            "nikobus_refreshed", {"impacted_module_address": module_id}
        )

    async def _delayed_stop(
        self,
        module_id: str,
        sent_state: bytearray,
        indexes: set[int],
        delay: float,
        token: str,
    ) -> None:
        """Send the bus stop once travel time elapses.

        Composes the stop frame from the module's CURRENT state at timeout,
        not the activation snapshot: a channel redirected during travel (by a
        wall button, another scene, or a direct cover) is left alone — only
        channels still doing what we commanded are forced to STOPPED.
        """
        await asyncio.sleep(delay)
        if self._module_tokens.get(module_id) != token:
            return

        current = self.coordinator.nikobus_module_states.get(module_id)
        stop_state = bytearray(current) if current else bytearray(sent_state)
        changed = False
        for idx in indexes:
            if idx < len(stop_state) and stop_state[idx] == sent_state[idx]:
                stop_state[idx] = STATE_STOPPED
                changed = True
        if not changed:
            return

        try:
            await self._commit_module(module_id, stop_state)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error("CF cover %s: timed stop failed for %s: %s", self._bus_address, module_id, err)

    async def _stop(self) -> None:
        """Stop all members now: cancel timers, commit STOPPED per module."""
        self._cancel_motion()
        self._cancel_stops()

        self._calculator.stop()
        self._position = self._calculator.current_position()
        self._state = STATE_STOPPED

        for module_id, members in self._members_by_module().items():
            current = self.coordinator.nikobus_module_states.get(module_id, bytearray(12))
            state = bytearray(current)
            for member in members:
                idx = member["channel"] - 1
                if 0 <= idx < len(state):
                    state[idx] = STATE_STOPPED
            await self._commit_module(module_id, state)

        self.async_write_ha_state()

    def _start_motion(self, direction: str) -> None:
        """Start the group-level position model for the UI."""
        self._cancel_motion()
        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
        active = (
            self._calculator.time_up if direction == "opening" else self._calculator.time_down
        )
        self._calculator.start_travel(direction)
        self._position = self._calculator.current_position()
        self._run_limit = max(DEFAULT_COVER_MOVEMENT_BUFFER, active)
        self._motion_task = self.hass.async_create_task(self._motion_loop())
        self.async_write_ha_state()

    async def _motion_loop(self) -> None:
        """Advance the displayed position until travel completes.

        Does not send bus frames — the hardware stop is the job of the
        per-module timed-stop tasks — so this loop can be cancelled freely
        without ever swallowing a STOP command.
        """
        try:
            start = time.monotonic()
            while self._state in (STATE_OPENING, STATE_CLOSING):
                elapsed = time.monotonic() - start
                self._position = self._calculator.current_position()
                if elapsed >= self._run_limit:
                    self._position = 100.0 if self._state == STATE_OPENING else 0.0
                    self._calculator.set_position(self._position)
                    self._state = STATE_STOPPED
                    self.async_write_ha_state()
                    break
                self.async_write_ha_state()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    def _cancel_motion(self) -> None:
        task = self._motion_task
        self._motion_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _cancel_stops(self) -> None:
        self._module_tokens.clear()
        for task in self._stop_tasks:
            task.cancel()
        self._stop_tasks.clear()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Group cover has no single bus channel — ignore poll updates.

        Its state is driven entirely by HA actions and the local motion
        model; reading a per-channel byte back would be ambiguous across
        members, so we deliberately don't react to coordinator refreshes.
        """
