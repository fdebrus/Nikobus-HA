"""Nikobus Button Press Events Handling"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from nikobus_connect.discovery import find_module, find_operation_point

from .const import (
    BURST_DETECT_GAP_COUNT,
    BURST_GAP_THRESHOLD_S,
    BURST_RECENT_GAPS_WINDOW,
    BUTTON_TIMER_THRESHOLDS,
    DIMMER_DELAY,
    EVENT_BUTTON_OPERATION,
    EVENT_BUTTON_PRESSED,
    FRAME_CADENCE_S,
    MAX_EXTENDED_RELEASE_MS,
    REFRESH_DELAY,
    RELEASE_THRESHOLD_MS,
    SHORT_PRESS,
    operation_signal,
    press_signal,
)

if TYPE_CHECKING:
    from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class PressState:
    """Track the state of an in-flight button press.

    Duration is anchored to ``frame_count`` (each received frame
    represents ``FRAME_CADENCE_S`` of held time on the wire), not
    wall-clock — that's the only invariant that survives upstream
    buffering. ``recent_gaps`` and ``current_release_threshold_ms``
    drive burst-aware release patience: when frames arrive faster
    than the wire could deliver them (gap < ``BURST_GAP_THRESHOLD_S``),
    we know a bridge stall just drained into us and extend the
    release threshold to absorb the next likely stall.
    """
    address: str
    press_start: float
    last_press_time: float
    press_id: str
    module_address: str | None
    channel: int | None
    release_task: asyncio.Task[None] | None = None
    last_timer_threshold: int = 0
    frame_count: int = 1
    recent_gaps: deque[float] = field(
        default_factory=lambda: deque(maxlen=BURST_RECENT_GAPS_WINDOW)
    )
    current_release_threshold_ms: float = float(RELEASE_THRESHOLD_MS)


class NikobusActuator:
    """Handles button press events and triggers targeted module refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        dict_button_data: dict[str, Any],
        module_data: dict[str, Any],
    ) -> None:
        """Initialize the Nikobus actuator.

        ``module_data`` is the live caller-owned dict wrapped by the Store
        (``{"nikobus_module": {addr: entry}}``). We hold a reference rather
        than a copy so ``on_module_save`` mutations are visible immediately.
        """
        self._hass = hass
        self._coordinator = coordinator
        self._dict_button_data = dict_button_data
        self._module_data = module_data
        self._press_states: dict[str, PressState] = {}
        self._module_refresh_tasks: dict[str, asyncio.Task[None]] = {}

    def stop(self) -> None:
        """Cancel all in-flight press-release and module-refresh tasks.

        Called from ``NikobusDataCoordinator.stop()`` so a config-entry
        unload/reload doesn't leave a button-press handler running against
        a torn-down command handler / connection. Tasks self-terminate in
        a few seconds anyway, but cancelling makes teardown deterministic.
        """
        for state in self._press_states.values():
            if state.release_task and not state.release_task.done():
                state.release_task.cancel()
        self._press_states.clear()
        for task in self._module_refresh_tasks.values():
            if not task.done():
                task.cancel()
        self._module_refresh_tasks.clear()

    async def handle_button_press(self, address: str) -> None:
        """Handle incoming button frames with frame-count duration tracking.

        Each frame is treated as ``FRAME_CADENCE_S`` (40 ms) of held
        time on the wire — the only quantity that survives upstream
        buffering correctly. Burst-flushed frames update the burst
        window so the release detector can extend its patience.
        """
        normalized_address = address.upper()
        current_time = time.monotonic()

        if normalized_address in self._press_states:
            state = self._press_states[normalized_address]
            gap = current_time - state.last_press_time
            state.last_press_time = current_time
            state.frame_count += 1
            state.recent_gaps.append(gap)
            self._update_release_threshold(state)
            self._maybe_fire_frame_count_timers(state)
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

        # Start background task for release detection. Timer events
        # are now fired synchronously inside handle_button_press as
        # frame_count crosses each threshold (see
        # _maybe_fire_frame_count_timers) — no async sleeps needed.
        state.release_task = self._hass.async_create_task(self._wait_for_release(state))

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

    def _maybe_fire_frame_count_timers(self, state: PressState) -> None:
        """Fire ``nikobus_button_timer_N`` events when frame_count
        crosses each long-press threshold.

        Anchored to ``frame_count * FRAME_CADENCE_S`` so timers fire
        correctly even when frames arrive in a buffered burst (where
        wall-clock would never reach the threshold within the press
        window). Idempotent via ``last_timer_threshold``.
        """
        elapsed_s = state.frame_count * FRAME_CADENCE_S
        for threshold in BUTTON_TIMER_THRESHOLDS:
            if state.last_timer_threshold >= threshold:
                continue
            if elapsed_s < threshold:
                continue
            state.last_timer_threshold = threshold
            self._fire_event(
                f"nikobus_button_timer_{threshold}",
                state,
                state_value="timer",
                duration=elapsed_s,
                threshold=threshold,
            )

    def _update_release_threshold(self, state: PressState) -> None:
        """Adjust the per-press release threshold based on burst signal.

        When several recent inter-frame gaps are below
        ``BURST_GAP_THRESHOLD_S``, frames are arriving faster than the
        wire can deliver them — a bridge stall has just drained into
        us. Extend the release threshold to absorb the likely next
        stall, scaled to the implied bridge buffer (frame_count *
        cadence ≈ ms of wire time the bridge withheld). Cap at
        ``MAX_EXTENDED_RELEASE_MS`` to keep release latency bounded.

        When recent gaps look normal again (no burst markers in the
        window), the threshold relaxes back to ``RELEASE_THRESHOLD_MS``
        so a real release on a healthy bridge is detected promptly.
        """
        burst_gap_count = sum(
            1 for g in state.recent_gaps if g < BURST_GAP_THRESHOLD_S
        )
        if burst_gap_count >= BURST_DETECT_GAP_COUNT:
            implied_stall_ms = state.frame_count * FRAME_CADENCE_S * 1000.0
            state.current_release_threshold_ms = min(
                float(MAX_EXTENDED_RELEASE_MS),
                max(state.current_release_threshold_ms, implied_stall_ms),
            )
        elif burst_gap_count == 0 and len(state.recent_gaps) >= BURST_RECENT_GAPS_WINDOW:
            # The full recent-gap window is clean — bridge is back to
            # normal cadence. Relax patience back to baseline so a
            # real release isn't held up by stale burst state.
            state.current_release_threshold_ms = float(RELEASE_THRESHOLD_MS)

    async def _wait_for_release(self, state: PressState) -> None:
        """Monitor for the absence of button frames to detect a release.

        Uses the per-press adaptive release threshold so a burst-flush
        followed by silence isn't misclassified as a release. Press
        duration is computed from frame_count (wire-time anchor)
        rather than wall-clock — see ``PressState`` docstring.
        """
        try:
            while True:
                await asyncio.sleep(0.05)
                active_state = self._press_states.get(state.address)
                if not active_state or active_state.press_id != state.press_id:
                    return

                silence_ms = (time.monotonic() - active_state.last_press_time) * 1000
                if silence_ms >= active_state.current_release_threshold_ms:
                    duration = active_state.frame_count * FRAME_CADENCE_S
                    await self._handle_release(active_state, duration)
                    return
        except asyncio.CancelledError:
            pass

    async def _handle_release(self, state: PressState, press_duration: float) -> None:
        """Cleanup and process module updates upon button release."""
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
        hit = find_operation_point(self._dict_button_data, address)
        if hit is None:
            _LOGGER.info("Press from unknown button %s — run discovery to populate it", address)
            return
        _physical_addr, _key_label, op_point = hit
        await self.process_button_modules(op_point, address, press_context)

    def _derive_impacted_modules(self, op_point: dict[str, Any]) -> list[tuple[str, str]]:
        """Return the unique (module_address, group) pairs this op-point affects.

        Derived from ``linked_modules`` — channels 1-6 live in feedback group 1,
        7-12 in group 2.
        """
        seen: set[tuple[str, str]] = set()
        for link in op_point.get("linked_modules") or []:
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

    async def process_button_modules(self, op_point: dict[str, Any], button_address: str, press_context: dict[str, Any] | None) -> None:
        """Refresh states for specific modules impacted by this op-point."""
        press_id = (press_context or {}).get("press_id") or f"{button_address}-{uuid.uuid4().hex[:8]}"

        impacted = self._derive_impacted_modules(op_point)
        _LOGGER.debug("[%s] Button %s impacts %d module(s)", press_id, button_address, len(impacted))

        for addr, group in impacted:

            # Determine if this specific module is a dimmer BEFORE debouncing.
            hit = find_module(self._module_data, addr)
            is_dimmer = hit is not None and hit[1].get("module_type") == "dimmer_module"
            requires_long_press = is_dimmer
            is_initial_press = press_context is not None and press_context.get("duration_s") == 0.0

            if requires_long_press and is_initial_press:
                _LOGGER.debug("[%s] Dimmer %s group %s — ignoring initial press, waiting for release", press_id, addr, group)
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
                _LOGGER.debug("[%s] Cancelling pending refresh for module %s group %s", press_id, addr, group)
                self._module_refresh_tasks[cache_key].cancel()

            # ==========================================
            # 3. Delayed State Fetch Task (UI Update Only)
            # ==========================================
            async def _refresh_task(m_addr=addr, m_group=group, m_press_id=press_id, m_requires_long_press=requires_long_press):
                try:
                    # STEP 1: Immediate UI Update (Skip for dimmers)
                    if not m_requires_long_press:
                        _LOGGER.debug("[%s] Immediate refresh of module %s group %s", m_press_id, m_addr, m_group)
                        await asyncio.sleep(0.3)

                        # Skip while the button is still held — a read now
                        # would collide on the bus; defer to the release.
                        if button_address.upper() in self._press_states:
                            _LOGGER.debug("[%s] Button still held — deferring refresh to release", m_press_id)
                            return

                        try:
                            new_state = await self._coordinator.nikobus_command.get_output_state(m_addr, m_group)
                            if new_state:
                                _LOGGER.debug("[%s] Module %s read %s on immediate refresh", m_press_id, m_addr, new_state)
                                self._coordinator.set_bytearray_group_state(m_addr, m_group, new_state)
                                await self._coordinator.async_event_handler("nikobus_refreshed", {"impacted_module_address": m_addr})
                        except asyncio.CancelledError:
                            raise
                        except Exception as err:
                            _LOGGER.debug("[%s] Immediate refresh of module %s failed: %s", m_press_id, m_addr, err)

                    # Read again once the outputs have settled.
                    delay = DIMMER_DELAY if m_requires_long_press else max(0, REFRESH_DELAY - 0.3)
                    _LOGGER.debug("[%s] Waiting %.1fs for module %s to settle", m_press_id, delay, m_addr)

                    await asyncio.sleep(delay)

                    _LOGGER.debug("[%s] Reading settled state of module %s group %s", m_press_id, m_addr, m_group)
                    new_state = await self._coordinator.nikobus_command.get_output_state(m_addr, m_group)

                    if new_state:
                        _LOGGER.debug("[%s] Module %s settled at %s", m_press_id, m_addr, new_state)
                        self._coordinator.set_bytearray_group_state(m_addr, m_group, new_state)
                        await self._coordinator.async_event_handler("nikobus_refreshed", {"impacted_module_address": m_addr})
                    else:
                        _LOGGER.warning("[%s] Module %s returned an empty settled state", m_press_id, m_addr)

                except asyncio.CancelledError:
                    _LOGGER.debug("[%s] Refresh of module %s cancelled by a newer press", m_press_id, m_addr)
                    return
                except Exception as err:
                    _LOGGER.error("[%s] Failed to refresh module %s group %s: %s", m_press_id, m_addr, m_group, err)
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
            "ts": datetime.now(timezone.utc).isoformat(),
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
        _LOGGER.debug("[%s] Fire %s — %s", state.press_id, event_type, payload)

        self._hass.bus.async_fire(event_type, payload)

        # Internal per-address wake alongside the public bus event, so a
        # notification reaches only the entities of the addresses it
        # concerns rather than every output / button entity filtering a
        # shared event (see operation_signal / press_signal). Automations
        # still consume the bus events fired above.
        if event_type == EVENT_BUTTON_OPERATION:
            if module := payload.get("impacted_module_address"):
                async_dispatcher_send(self._hass, operation_signal(module))
        elif event_type == EVENT_BUTTON_PRESSED:
            seen: set[str] = set()
            for key in ("address", "module_address"):
                addr = payload.get(key)
                if addr and addr not in seen:
                    seen.add(addr)
                    async_dispatcher_send(self._hass, press_signal(addr), payload)

    def _derive_button_context(self, address: str) -> tuple[str | None, int | None]:
        """Determine the primary (module_address, channel) link from discovery."""
        hit = find_operation_point(self._dict_button_data, address)
        if hit is None:
            return (None, None)
        _physical_addr, _key_label, op_point = hit
        for link in op_point.get("linked_modules") or []:
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
