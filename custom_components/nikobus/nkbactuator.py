"""Key presses seen on the bus, turned into Home Assistant events and refreshes.

What a press *is* — frames, wire-time duration, hold milestones, burst
patience, release — is the library's business: ``nikobus_connect.press``
infers it from the ``#N`` frames and knows nothing of Home Assistant.
This module is the glue around it: it feeds the tracker, watches for
release from the event loop, fires the ``nikobus_button_*`` events and
per-address signals, and reads the modules a key drives once the
outputs have had time to move.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from nikobus_connect.discovery import find_module, find_operation_point
from nikobus_connect.press import (
    PressState,
    PressTracker,
    impacted_groups,
    primary_link,
)

from .const import (
    DIMMER_DELAY,
    EVENT_BUTTON_OPERATION,
    EVENT_BUTTON_PRESSED,
    POLLED_MODULE_TYPES,
    REFRESH_DELAY,
    operation_signal,
    press_signal,
)

if TYPE_CHECKING:
    from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

#: How often the release watcher asks the tracker whether a press ended.
RELEASE_POLL_S = 0.05


@dataclass
class _PressContext:
    """What Home Assistant adds to a press the tracker follows."""

    module_address: str | None
    channel: int | None
    release_task: asyncio.Task[None] | None = None


class NikobusActuator:
    """Handles button press events and triggers targeted module refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        dict_button_data: dict[str, Any],
        module_data: dict[str, Any],
        *,
        tracker: PressTracker | None = None,
    ) -> None:
        """Initialize the Nikobus actuator.

        ``module_data`` is the live caller-owned dict wrapped by the Store
        (``{"nikobus_module": {addr: entry}}``). We hold a reference rather
        than a copy so ``on_module_save`` mutations are visible immediately.
        ``tracker`` is injectable for tests that drive the clock.
        """
        self._hass = hass
        self._coordinator = coordinator
        self._dict_button_data = dict_button_data
        self._module_data = module_data
        self.tracker = tracker or PressTracker()
        self._contexts: dict[str, _PressContext] = {}
        self._module_refresh_tasks: dict[str, asyncio.Task[None]] = {}

    def stop(self) -> None:
        """Cancel all in-flight press-release and module-refresh tasks.

        Called from ``NikobusDataCoordinator.stop()`` so a config-entry
        unload/reload doesn't leave a button-press handler running against
        a torn-down command handler / connection. Tasks self-terminate in
        a few seconds anyway, but cancelling makes teardown deterministic.
        """
        for context in self._contexts.values():
            if context.release_task and not context.release_task.done():
                context.release_task.cancel()
        self._contexts.clear()
        self.tracker.clear()
        for task in self._module_refresh_tasks.values():
            if not task.done():
                task.cancel()
        self._module_refresh_tasks.clear()

    # ------------------------------------------------------------------
    # Inbound frames
    # ------------------------------------------------------------------

    async def handle_button_press(self, address: str) -> None:
        """Account for one ``#N`` frame: start a press, or extend it."""
        state, started, timers = self.tracker.frame(address)
        if started:
            module_address, channel = self._derive_button_context(state.address)
            context = _PressContext(module_address, channel)
            self._contexts[state.address] = context
            context.release_task = self._hass.async_create_task(
                self._wait_for_release(state)
            )
            self._fire_event(
                EVENT_BUTTON_PRESSED, state, context, state_value="pressed", duration=None
            )
            # Read the impacted modules right away; the release re-reads.
            self._hass.async_create_task(
                self.button_discovery(
                    state.address,
                    press_context={
                        "press_id": state.press_id,
                        "duration_s": 0.0,
                        "module_address": module_address,
                        "channel": channel,
                        "bucket": 0,
                    },
                )
            )
        for threshold in timers:
            self._fire_event(
                f"nikobus_button_timer_{threshold}",
                state,
                self._contexts.get(state.address),
                state_value="timer",
                duration=state.duration_s,
                threshold=threshold,
            )

    async def _wait_for_release(self, state: PressState) -> None:
        """Watch for the silence that ends this press, then release it."""
        try:
            while True:
                await asyncio.sleep(RELEASE_POLL_S)
                if self.tracker.active(state.address) is not state:
                    return
                if await self._release_if_due(state):
                    return
        except asyncio.CancelledError:
            pass

    async def _release_if_due(self, state: PressState) -> bool:
        """Release ``state`` if its silence has outlasted its patience."""
        if self.tracker.release_due(state.address, state.press_id) is None:
            return False
        await self._handle_release(state)
        return True

    async def _handle_release(self, state: PressState) -> None:
        """Fire the release events and schedule the settled read."""
        context = self._contexts.get(state.address)
        duration = state.duration_s
        bucket = state.bucket
        self._fire_event("nikobus_button_released", state, context, state_value="released", duration=duration, bucket=bucket)
        event_name = "nikobus_short_button_pressed" if state.is_short else "nikobus_long_button_pressed"
        self._fire_event(event_name, state, context, state_value="released", duration=duration, bucket=bucket)
        self._fire_event(f"nikobus_button_pressed_{bucket}", state, context, state_value="released", duration=duration, bucket=bucket)

        press_context = {
            "press_id": state.press_id,
            "duration_s": duration,
            "module_address": context.module_address if context else None,
            "channel": context.channel if context else None,
            "bucket": bucket,
        }
        self.tracker.end(state.address, state.press_id)
        self._contexts.pop(state.address, None)
        self._hass.async_create_task(self.button_discovery(state.address, press_context=press_context))

    # ------------------------------------------------------------------
    # What a press reaches
    # ------------------------------------------------------------------

    async def button_discovery(self, address: str, press_context: dict[str, Any] | None = None) -> None:
        """Identify impacted modules and trigger targeted refreshes."""
        hit = find_operation_point(self._dict_button_data, address)
        if hit is None:
            _LOGGER.info("Press from unknown button %s — run discovery to populate it", address)
            return
        _physical_addr, _key_label, op_point = hit
        await self.process_button_modules(op_point, address, press_context)

    async def refresh_after_host_press(self, address: str) -> None:
        """Refresh the modules a press *sent by Home Assistant* impacts.

        The interface never relays the host's own ``#N`` back, so the
        inbound path (``handle_button_press`` → ``button_discovery``)
        never runs for it and the impacted outputs were only read at the
        next poll or feedback push — minutes, on a pushed installation.
        Same delayed reads and debounce keys as an inbound press; no
        ``nikobus_button_operation`` event, since nobody pressed a key.
        """
        hit = find_operation_point(self._dict_button_data, address.upper())
        if hit is None:
            _LOGGER.debug("Host press of %s: no operation point known, nothing to refresh", address)
            return
        _physical_addr, _key_label, op_point = hit
        press_id = f"host-{address.upper()}-{uuid.uuid4().hex[:8]}"
        await self.process_button_modules(
            op_point, address.upper(), {"press_id": press_id}, fire_event=False
        )

    def _derive_impacted_modules(self, op_point: dict[str, Any]) -> list[tuple[str, str]]:
        """The ``(module_address, group)`` pairs this op-point affects and
        whose state is read at all.

        The library lists what the links drive; this keeps only modules
        of a polled type. An audio trigger's op point links to the Audio
        Distribution module that listens for it, and that module never
        answers a state query: reading it after every press was three
        attempts of five seconds and an error, for nothing. A module the
        store does not know is kept — it may simply not have been
        classified yet.
        """
        impacted: list[tuple[str, str]] = []
        for module_address, group in impacted_groups(op_point):
            hit = find_module(self._module_data, module_address)
            module_type = hit[1].get("module_type") if hit else None
            if module_type is not None and module_type not in POLLED_MODULE_TYPES:
                _LOGGER.debug(
                    "Module %s is a %s — its state is never read, not refreshing it",
                    module_address,
                    module_type,
                )
                continue
            impacted.append((module_address, str(group)))
        return impacted

    async def process_button_modules(
        self,
        op_point: dict[str, Any],
        button_address: str,
        press_context: dict[str, Any] | None,
        *,
        fire_event: bool = True,
    ) -> None:
        """Refresh states for specific modules impacted by this op-point.

        ``fire_event=False`` (a press Home Assistant sent itself) skips
        the ``nikobus_button_operation`` event and only schedules the
        reads.
        """
        press_id = (press_context or {}).get("press_id") or f"{button_address}-{uuid.uuid4().hex[:8]}"
        button_address = button_address.upper()

        impacted = self._derive_impacted_modules(op_point)
        _LOGGER.debug("[%s] Button %s impacts %d module(s)", press_id, button_address, len(impacted))

        for addr, group in impacted:
            # A dimmer moves while the key is held: read it on release only.
            hit = find_module(self._module_data, addr)
            requires_long_press = hit is not None and hit[1].get("module_type") == "dimmer_module"
            is_initial_press = press_context is not None and press_context.get("duration_s") == 0.0
            if requires_long_press and is_initial_press:
                _LOGGER.debug("[%s] Dimmer %s group %s — ignoring initial press, waiting for release", press_id, addr, group)
                continue

            if fire_event:
                self._fire_event(
                    EVENT_BUTTON_OPERATION,
                    PressState(address=button_address, press_id=press_id, started_at=0.0, last_frame_at=0.0),
                    _PressContext(addr, None),
                    state_value="released",
                    duration=(press_context or {}).get("duration_s"),
                    bucket=(press_context or {}).get("bucket"),
                    extra={
                        "impacted_module_address": addr,
                        "impacted_module_group": group,
                    },
                )

            # One pending refresh per module group: a newer press replaces it.
            cache_key = f"{addr}_{group}"
            if cache_key in self._module_refresh_tasks:
                _LOGGER.debug("[%s] Cancelling pending refresh for module %s group %s", press_id, addr, group)
                self._module_refresh_tasks[cache_key].cancel()

            async def _refresh_task(
                m_addr: str = addr,
                m_group: str = group,
                m_press_id: str = press_id,
                m_requires_long_press: bool = requires_long_press,
                # Bound at definition time like the others — the
                # finally-block below runs after awaits, when the
                # enclosing loop variable may already have moved on
                # to another module's key (ruff B023).
                m_cache_key: str = cache_key,
            ) -> None:
                try:
                    # An immediate read for the relay modules; a dimmer waits.
                    if not m_requires_long_press:
                        _LOGGER.debug("[%s] Immediate refresh of module %s group %s", m_press_id, m_addr, m_group)
                        await asyncio.sleep(0.3)

                        # Skip while the button is still held — a read now
                        # would collide on the bus; defer to the release.
                        if button_address in self.tracker:
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
                        except Exception as err:  # noqa: BLE001 - defensive: keep the refresh loop alive
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
                except Exception as err:  # noqa: BLE001 - defensive: keep the refresh loop alive
                    _LOGGER.error("[%s] Failed to refresh module %s group %s: %s", m_press_id, m_addr, m_group, err)
                finally:
                    if self._module_refresh_tasks.get(m_cache_key) == asyncio.current_task():
                        self._module_refresh_tasks.pop(m_cache_key, None)

            self._module_refresh_tasks[cache_key] = self._hass.async_create_task(_refresh_task())

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _fire_event(
        self,
        event_type: str,
        state: PressState,
        context: _PressContext | None,
        **kwargs: Any,
    ) -> None:
        """Fire one ``nikobus_button_*`` event with the shared payload."""
        payload: dict[str, Any] = {
            "address": state.address,
            "module_address": context.module_address if context else None,
            "channel": context.channel if context else None,
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
        """The primary ``(module_address, channel)`` a key drives, for the events."""
        hit = find_operation_point(self._dict_button_data, address)
        if hit is None:
            return (None, None)
        _physical_addr, _key_label, op_point = hit
        return primary_link(op_point)
