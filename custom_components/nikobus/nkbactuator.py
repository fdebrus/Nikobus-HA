"""Nikobus Button Press Events Handling - Platinum Edition."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, Any

from homeassistant.core import HomeAssistant
from .exceptions import NikobusTimeoutError
from .const import BUTTON_TIMER_THRESHOLDS, DIMMER_DELAY, REFRESH_DELAY, SHORT_PRESS

_LOGGER = logging.getLogger(__name__)

# Event Constants
BUTTON_OPERATION_EVENT = "nikobus_button_operation"
EVENT_BUTTON_PRESSED = "nikobus_button_pressed"


@dataclass
class PressState:
    """Track the state of an in-flight button press."""
    address: str
    press_start: float
    last_press_time: float
    press_id: str
    module_address: Optional[str]
    channel: Optional[int]
    release_task: Optional[asyncio.Task] = None
    timer_tasks: Dict[int, asyncio.Task] = field(default_factory=dict)
    last_timer_threshold: int = 0


class NikobusActuator:
    """Handles button press events and triggers targeted module refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: Any,
        dict_button_data: dict,
        dict_module_data: dict,
    ) -> None:
        """Initialize the Nikobus actuator."""
        self._hass = hass
        self._coordinator = coordinator
        self._dict_button_data = dict_button_data
        self._dict_module_data = dict_module_data
        self._debounce_time_ms = 150
        self._press_states: Dict[str, PressState] = {}
        self._last_press_context: Dict[str, Dict[str, Any]] = {}

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

        # NEW: Trigger module state synchronization IMMEDIATELY upon press
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
        
        self._fire_event("nikobus_button_released", state, state_value="released", duration=press_duration)

        press_context = {
            "press_id": state.press_id,
            "duration_s": press_duration,
            "module_address": state.module_address,
            "channel": state.channel,
            "bucket": self._get_bucket(press_duration),
        }

        # Fire classification events (Short vs Long)
        event_name = "nikobus_short_button_pressed" if press_duration < SHORT_PRESS else "nikobus_long_button_pressed"
        self._fire_event(event_name, state, state_value="released", duration=press_duration)

        # Trigger module state synchronization
        self._hass.async_create_task(self.button_discovery(state.address, press_context=press_context))
        self._press_states.pop(state.address, None)

    async def button_discovery(self, address: str, press_context: Optional[Dict[str, Any]] = None) -> None:
        """Identify impacted modules and trigger targeted refreshes."""
        button_data = self._dict_button_data.get("nikobus_button", {}).get(address)

        if button_data:
            await self.process_button_modules(button_data, address, press_context)
        else:
            # Auto-discovery for unconfigured buttons
            new_button = {
                "description": f"DISCOVERED - Nikobus Button #N{address}",
                "address": address,
                "impacted_module": [{"address": "", "group": ""}],
            }
            self._dict_button_data.setdefault("nikobus_button", {})[address] = new_button
            await self._coordinator.nikobus_config.write_json_data("nikobus_button_config.json", "button", self._dict_button_data)

    async def process_button_modules(self, button_data: Dict[str, Any], button_address: str, press_context: Optional[Dict[str, Any]]) -> None:
        """Refresh states for specific modules impacted by this button."""
        op_time = float(button_data.get("operation_time", 0))
        press_id = (press_context or {}).get("press_id") or f"{button_address}-{uuid.uuid4().hex[:8]}"
        
        impacted_modules = button_data.get("impacted_module", [])
        
        # Initialize the tracker for pending refresh tasks
        if not hasattr(self, "_module_refresh_tasks"):
            self._module_refresh_tasks = {}

        for module_info in impacted_modules:
            addr = module_info.get("address")
            group = module_info.get("group")
            if not addr or not group:
                continue

            # Debouncer: Cancel any pending refresh for this specific module/group combo
            cache_key = f"{addr}_{group}"
            if cache_key in self._module_refresh_tasks:
                self._module_refresh_tasks[cache_key].cancel()

            async def _refresh_task(m_addr=addr, m_group=group, m_op_time=op_time, m_press_id=press_id):
                try:
                    # Check if the impacted module is a dimmer
                    is_dimmer = m_addr in self._dict_module_data.get("dimmer_module", {})
                    
                    # ==========================================
                    # STEP 1: Immediate UI Update (Skip for dimmers to avoid mid-fade states)
                    # ==========================================
                    if not is_dimmer:
                        await asyncio.sleep(0.3)
                        
                        try:
                            new_state = await self._coordinator.nikobus_command.get_output_state(m_addr, m_group)
                            if new_state:
                                self._coordinator.set_bytearray_group_state(m_addr, m_group, new_state)
                                await self._coordinator.async_event_handler("nikobus_refreshed", {"impacted_module_address": m_addr})
                        except Exception as err:
                            # If the bus is busy, just ignore it. Step 2 will catch the final state anyway.
                            _LOGGER.debug("Quick fetch failed for %s, waiting for delayed fetch: %s", m_addr, err)

                    # ==========================================
                    # STEP 2: Delayed Check for Settled State
                    # ==========================================
                    if is_dimmer:
                        # Dimmers need their full fade time before we check the state
                        await asyncio.sleep(DIMMER_DELAY)
                    else:
                        # Relays/Covers subtract the 0.3s we already waited in Step 1
                        await asyncio.sleep(max(0, REFRESH_DELAY - 0.3))

                    new_state = await self._coordinator.nikobus_command.get_output_state(m_addr, m_group)
                    if new_state:
                        self._coordinator.set_bytearray_group_state(m_addr, m_group, new_state)
                        await self._coordinator.async_event_handler("nikobus_refreshed", {"impacted_module_address": m_addr})

                    # Fire the rich event for covers/complex entities
                    self._fire_event(
                        BUTTON_OPERATION_EVENT,
                        PressState(button_address.upper(), 0, 0, m_press_id, m_addr, None),
                        state_value="released",
                        duration=(press_context or {}).get("duration_s"),
                        extra={
                            "button_operation_time": m_op_time,
                            "impacted_module_address": m_addr,
                            "impacted_module_group": m_group,
                        }
                    )

                except asyncio.CancelledError:
                    # A new button press canceled this task. Exit cleanly.
                    return 
                except Exception as err:
                    _LOGGER.error("Error refreshing module %s: %s", m_addr, err)

            # Schedule the newly requested refresh
            self._module_refresh_tasks[cache_key] = self._hass.async_create_task(_refresh_task())

    def _fire_event(self, event_type: str, state: PressState, **kwargs) -> None:
        """Helper to fire standardized Nikobus events."""
        payload = {
            "address": state.address,
            "module_address": state.module_address,
            "channel": state.channel,
            "press_id": state.press_id,
            "state": kwargs.get("state_value"),
            "duration_s": kwargs.get("duration"),
            "bucket": kwargs.get("bucket"),
            "threshold_s": kwargs.get("threshold"),
            "source": "nikobus",
        }
        if extra := kwargs.get("extra"):
            payload.update(extra)
            
        self._hass.bus.async_fire(event_type, payload)

    def _derive_button_context(self, address: str) -> Tuple[Optional[str], Optional[int]]:
        """Determine primary module/channel link for a button from config."""
        button_data = self._dict_button_data.get("nikobus_button", {}).get(address, {})
        impacted = button_data.get("impacted_module") or []
        links = button_data.get("discovered_link") or []
        
        module_addr = impacted[0].get("address") if impacted else (links[0].get("module_address") if links else None)
        channel = None
        if links and (ch_str := links[0].get("channel")):
            # Extract digits from string safely (e.g. "Channel 12" -> 12)
            parts = ch_str.split()
            channel = int(parts[-1]) if parts and parts[-1].isdigit() else None
            
        return (module_addr.upper() if module_addr else None, channel)

    def _get_bucket(self, duration: float) -> int:
        """Map press duration to a discrete bucket (0-3)."""
        return min(int(duration), 3)