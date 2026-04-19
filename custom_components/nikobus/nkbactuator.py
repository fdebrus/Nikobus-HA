"""Nikobus Button Press Events Handling"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timezone as _tz
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from .const import (
    BUTTON_TIMER_THRESHOLDS,
    DIMMER_DELAY,
    EVENT_BUTTON_OPERATION,
    EVENT_BUTTON_PRESSED,
    REFRESH_DELAY,
    SHORT_PRESS,
)

if TYPE_CHECKING:
    from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class PressState:
    """Track the state of an in-flight button press."""
    address: str
    press_start: float
    last_press_time: float
    press_id: str
    module_address: str | None
    channel: int | None
    release_task: asyncio.Task[None] | None = None
    timer_tasks: dict[int, asyncio.Task[None]] = field(default_factory=dict)
    last_timer_threshold: int = 0


class NikobusActuator:
    """Handles button press events and triggers targeted module refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        dict_button_data: dict[str, Any],
        dict_module_data: dict[str, Any],
    ) -> None:
        """Initialize the Nikobus actuator."""
        self._hass = hass
        self._coordinator = coordinator
        self._dict_button_data = dict_button_data
        self._dict_module_data = dict_module_data
        self._debounce_time_ms = 150
        self._press_states: dict[str, PressState] = {}
        self._module_refresh_tasks: dict[str, asyncio.Task[None]] = {}

    async def handle_button_press(self, address: str) -> None:
        """Handle incoming button frames with debounce and duration tracking."""
        normalized_address = address.upper()
        current_time = time.monotonic()

        if normalized_address in self._press_states:
            state = self._press_states[normalized_address]
            state.last_press_time = current_time
            return

        module_address, channel = self._derive_button_context(normalized_address)
        press_id = f"{normalized_address}-{current_time:.3f}-{uuid.uuid4().hex[:8]}"
        
        state = PressState(
            address=normalized_address,
            press_start=current_time,
            last_press_time=current_time,
            press_id=press_id,
            module_address=module_address,
            channel=channel,
        )
        self._press_states[normalized_address] = state

        # Start background tasks for release detection and duration timers
        state.release_task = self._hass.async_create_task(self._wait_for_release(state))
        self._start_timer_tasks(state)

        # Fire immediate press event for Binary Sensors
        self._fire_event(EVENT_BUTTON_PRESSED, state, state_value="pressed", duration=None)

        # Trigger module state synchronization IMMEDIATELY upon press
        press_context = {
            "press_id": state.press_id,
            "duration_s": 0.0,
            "module_address": state.module_address,
            "channel": state.channel,
            "bucket": 0,
        }
        self._hass.async_create_task(self.button_discovery(state.address, press_context=press_context))

    def _start_timer_tasks(self, state: PressState) -> None:
        """Initialize tasks for long-press duration thresholds."""
        for duration in BUTTON_TIMER_THRESHOLDS:
            task = self._hass.async_create_task(
                self._fire_timer_event(state.address, state.press_id, duration)
            )
            state.timer_tasks[duration] = task

    async def _fire_timer_event(self, address: str, press_id: str, duration: int) -> None:
        """Fire events when a press crosses a specific time threshold."""
        try:
            state = self._press_states.get(address)
            if state and state.press_id == press_id:
                elapsed = time.monotonic() - state.press_start
                await asyncio.sleep(max(duration - elapsed, 0))

            state = self._press_states.get(address)
            if not state or state.press_id != press_id or state.last_timer_threshold >= duration:
                return

            state.last_timer_threshold = duration
            self._fire_event(
                f"nikobus_button_timer_{duration}",
                state,
                state_value="timer",
                duration=time.monotonic() - state.press_start,
                threshold=duration,
            )
        except asyncio.CancelledError:
            pass

    async def _wait_for_release(self, state: PressState) -> None:
        """Monitor for the absence of button frames to detect a release."""
        try:
            while True:
                await asyncio.sleep(0.05)
                active_state = self._press_states.get(state.address)
                if not active_state or active_state.press_id != state.press_id:
                    return

                if (time.monotonic() - active_state.last_press_time) * 1000 >= self._debounce_time_ms:
                    duration = time.monotonic() - active_state.press_start
                    await self._handle_release(active_state, duration)
                    return
        except asyncio.CancelledError:
            pass

    async def _handle_release(self, state: PressState, press_duration: float) -> None:
        """Cleanup timers and process module updates upon button release."""
        for task in state.timer_tasks.values():
            task.cancel()
        
        bucket = self._get_bucket(press_duration)
        
        # 1. Base Release Event
        self._fire_event("nikobus_button_released", state, state_value="released", duration=press_duration, bucket=bucket)

        # 2. Classification Event (Short vs Long)
        event_name = "nikobus_short_button_pressed" if press_duration < SHORT_PRESS else "nikobus_long_button_pressed"
        self._fire_event(event_name, state, state_value="released", duration=press_duration, bucket=bucket)

        # 3. Explicit Bucket Event (0, 1, 2, 3)
        self._fire_event(f"nikobus_button_pressed_{bucket}", state, state_value="released", duration=press_duration, bucket=bucket)

        press_context = {
            "press_id": state.press_id,
            "duration_s": press_duration,
            "module_address": state.module_address,
            "channel": state.channel,
            "bucket": bucket,
        }

        # Trigger module state synchronization
        self._hass.async_create_task(self.button_discovery(state.address, press_context=press_context))
        self._press_states.pop(state.address, None)

    async def button_discovery(self, address: str, press_context: dict[str, Any] | None = None) -> None:
        """Identify impacted modules and trigger targeted refreshes."""
        button_data = self._dict_button_data.get("nikobus_button", {}).get(address)
        if button_data:
            await self.process_button_modules(button_data, address, press_context)
        else:
            _LOGGER.debug("Press from unknown button %s — run discovery to populate it", address)

    def _derive_impacted_modules(self, button_data: dict[str, Any]) -> list[tuple[str, str]]:
        """Return the unique (module_address, group) pairs this button affects.

        Derived from ``linked_modules`` — channels 1-6 live in feedback group 1,
        7-12 in group 2.
        """
        seen: set[tuple[str, str]] = set()
        for link in button_data.get("linked_modules") or []:
            if not isinstance(link, dict):
                continue
            module_address = (link.get("module_address") or "").upper()
            if not module_address:
                continue
            for out in link.get("outputs") or []:
                if not isinstance(out, dict):
                    continue
                channel = out.get("channel")
                if not isinstance(channel, int):
                    continue
                group = "1" if channel <= 6 else "2"
                seen.add((module_address, group))
        return list(seen)

    async def process_button_modules(self, button_data: dict[str, Any], button_address: str, press_context: dict[str, Any] | None) -> None:
        """Refresh states for specific modules impacted by this button."""
        press_id = (press_context or {}).get("press_id") or f"{button_address}-{uuid.uuid4().hex[:8]}"

        impacted = self._derive_impacted_modules(button_data)
        _LOGGER.debug("[%s] Processing button %s: %d modules impacted", press_id, button_address, len(impacted))

        for addr, group in impacted:

            # Determine if this specific module is a dimmer BEFORE debouncing
            is_dimmer = addr in self._dict_module_data.get("dimmer_module", {})
            requires_long_press = is_dimmer
            is_initial_press = press_context is not None and press_context.get("duration_s") == 0.0

            if requires_long_press and is_initial_press:
                _LOGGER.debug("[%s] Ignoring initial press for Dimmer %s (Group %s). Waiting for release.", press_id, addr, group)
                continue

            # ==========================================
            # 1. Fire Event IMMEDIATELY for HA Automations
            # ==========================================
            # 4. Post-refresh notification (nikobus_button_operation)
            self._fire_event(
                EVENT_BUTTON_OPERATION,
                PressState(button_address.upper(), 0, 0, press_id, addr, None),
                state_value="released",
                duration=(press_context or {}).get("duration_s"),
                bucket=(press_context or {}).get("bucket"),
                extra={
                    "impacted_module_address": addr,
                    "impacted_module_group": group,
                }
            )

            # ==========================================
            # 2. Strict Module Debouncer (Prevents UI Jumps)
            # ==========================================
            cache_key = f"{addr}_{group}"
            if cache_key in self._module_refresh_tasks:
                _LOGGER.debug("[%s] Canceling previous pending refresh for %s (Group %s)", press_id, addr, group)
                self._module_refresh_tasks[cache_key].cancel()

            # ==========================================
            # 3. Delayed State Fetch Task (UI Update Only)
            # ==========================================
            async def _refresh_task(m_addr=addr, m_group=group, m_press_id=press_id, m_requires_long_press=requires_long_press):
                try:
                    # STEP 1: Immediate UI Update (Skip for dimmers)
                    if not m_requires_long_press:
                        _LOGGER.debug("[%s] Step 1: Immediate refresh for %s (Group %s)", m_press_id, m_addr, m_group)
                        await asyncio.sleep(0.3)
                        
                        # Bus Clearance Check
                        if button_address.upper() in self._press_states:
                            _LOGGER.debug("[%s] Button still held. Aborting Step 1 to prevent collision. Deferring to release event.", m_press_id)
                            return
                        
                        try:
                            new_state = await self._coordinator.nikobus_command.get_output_state(m_addr, m_group)
                            if new_state:
                                _LOGGER.debug("[%s] Step 1 Success for %s: %s", m_press_id, m_addr, new_state)
                                self._coordinator.set_bytearray_group_state(m_addr, m_group, new_state)
                                await self._coordinator.async_event_handler("nikobus_refreshed", {"impacted_module_address": m_addr})
                        except asyncio.CancelledError:
                            raise
                        except Exception as err:
                            _LOGGER.debug("[%s] Step 1 Quick fetch failed for %s: %s", m_press_id, m_addr, err)

                    # STEP 2: Delayed Check for Settled State
                    delay = DIMMER_DELAY if m_requires_long_press else max(0, REFRESH_DELAY - 0.3)
                    _LOGGER.debug("[%s] Step 2: Waiting %.1fs for settled state on %s", m_press_id, delay, m_addr)
                    
                    await asyncio.sleep(delay)

                    _LOGGER.debug("[%s] Step 2: Requesting final state for %s (Group %s)", m_press_id, m_addr, m_group)
                    new_state = await self._coordinator.nikobus_command.get_output_state(m_addr, m_group)
                    
                    if new_state:
                        _LOGGER.debug("[%s] Step 2 Success for %s: %s", m_press_id, m_addr, new_state)
                        self._coordinator.set_bytearray_group_state(m_addr, m_group, new_state)
                        await self._coordinator.async_event_handler("nikobus_refreshed", {"impacted_module_address": m_addr})
                    else:
                        _LOGGER.warning("[%s] Step 2: Module %s returned empty state", m_press_id, m_addr)

                except asyncio.CancelledError:
                    _LOGGER.debug("[%s] Refresh task for %s was cancelled by a newer press", m_press_id, m_addr)
                    return 
                except Exception as err:
                    _LOGGER.error("[%s] Error refreshing module %s (Group %s): %s", m_press_id, m_addr, m_group, err)
                finally:
                    # Clean up the task reference when done
                    if self._module_refresh_tasks.get(cache_key) == asyncio.current_task():
                        self._module_refresh_tasks.pop(cache_key, None)

            # Schedule the newly requested refresh
            self._module_refresh_tasks[cache_key] = self._hass.async_create_task(_refresh_task())

    def _fire_event(self, event_type: str, state: PressState, **kwargs) -> None:
        """Helper to fire standardized Nikobus events and log them."""
        payload = {
            "address": state.address,
            "module_address": state.module_address,
            "channel": state.channel,
            "ts": datetime.now(_tz.utc).isoformat(),
            "press_id": state.press_id,
            "state": kwargs.get("state_value"),
            "duration_s": kwargs.get("duration"),
            "bucket": kwargs.get("bucket"),
            "threshold_s": kwargs.get("threshold"),
            "source": "nikobus",
        }
        if extra := kwargs.get("extra"):
            payload.update(extra)
            
        # Log the event exactly as it is fired to the Home Assistant bus
        _LOGGER.debug("[%s] Firing HA Event: %s | Payload: %s", state.press_id, event_type, payload)
        
        self._hass.bus.async_fire(event_type, payload)

    def _derive_button_context(self, address: str) -> tuple[str | None, int | None]:
        """Determine the primary (module_address, channel) link from discovery."""
        button_data = self._dict_button_data.get("nikobus_button", {}).get(address, {})
        for link in button_data.get("linked_modules") or []:
            if not isinstance(link, dict):
                continue
            module_addr = link.get("module_address")
            if not module_addr:
                continue
            channel: int | None = None
            outputs = link.get("outputs")
            if isinstance(outputs, list) and outputs:
                ch_val = outputs[0].get("channel")
                if isinstance(ch_val, int):
                    channel = ch_val
            return (module_addr.upper(), channel)
        return (None, None)

    def _get_bucket(self, duration: float) -> int:
        """Map press duration to a discrete bucket (0-3)."""
        return min(int(duration), 3)
