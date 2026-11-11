"""Key presses seen on the bus.

A held key repeats its telegram about every 40 ms, so a press is a burst
of frames and its release is the silence after it. The library's
``PressTracker`` does that arithmetic; this module turns its verdicts
into what Home Assistant needs: an event for the key's entity, a bus
event for automations on a bare address, and a read of the output
groups the key drives.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from nikobus_connect.press import PressState, PressTracker

from .const import (
    DIMMER_MODULE,
    DIMMER_READ_DELAY,
    DOMAIN,
    EVENT_PRESS,
    OUTPUT_MODULE_TYPES,
    PRESS_READ_DELAY,
    RELEASE_POLL,
    RELEASE_READ_DELAY,
    RGB_MODULE,
)

if TYPE_CHECKING:
    from .hub import NikobusHub

PRESSED = "pressed"
HOLD = "hold"
SHORT_RELEASE = "short_release"
LONG_RELEASE = "long_release"
EVENT_TYPES = [PRESSED, HOLD, SHORT_RELEASE, LONG_RELEASE]

KeyListener = Callable[[str, dict[str, Any]], None]


class PressHandler:
    """Follows every press from its first frame to its release."""

    def __init__(self, hub: NikobusHub) -> None:
        self._hub = hub
        self.tracker = PressTracker()
        self._listeners: dict[str, list[KeyListener]] = defaultdict(list)
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._reads: dict[tuple[str, int], asyncio.Task[None]] = {}

    @callback
    def subscribe(self, bus_address: str, listener: KeyListener) -> CALLBACK_TYPE:
        """Call ``listener(event_type, data)`` for the presses of one key."""
        self._listeners[bus_address].append(listener)
        return lambda: self._listeners[bus_address].remove(listener)

    def stop(self) -> None:
        """Forget the presses in flight and the reads they asked for."""
        for task in (*self._watchers.values(), *self._reads.values()):
            task.cancel()
        self._watchers.clear()
        self._reads.clear()
        self.tracker.clear()

    @callback
    def frame(self, address: str) -> None:
        """Account for one ``#N`` frame."""
        state, started, timers = self.tracker.frame(address)
        if started:
            self._hub.note_press(state.address)
            self._emit(state.address, PRESSED, {})
            self.schedule_reads(state.address, released=False)
            self._watchers[state.address] = self._hub.entry.async_create_background_task(
                self._hub.hass, self._async_watch(state), name=f"{DOMAIN}_press_{state.address}"
            )
        for seconds in timers:
            self._emit(state.address, HOLD, {"seconds": seconds})

    async def _async_watch(self, state: PressState) -> None:
        """Wait for the silence that ends a press."""
        while self.tracker.active(state.address) is state:
            await asyncio.sleep(RELEASE_POLL)
            if self.tracker.release_due(state.address, state.press_id) is None:
                continue
            self.tracker.end(state.address, state.press_id)
            self._watchers.pop(state.address, None)
            self._emit(
                state.address,
                SHORT_RELEASE if state.is_short else LONG_RELEASE,
                {"duration": round(state.duration_s, 2)},
            )
            self.schedule_reads(state.address, released=True)
            return

    def _emit(self, address: str, event_type: str, data: dict[str, Any]) -> None:
        for listener in list(self._listeners[address]):
            listener(event_type, data)
        self._hub.hass.bus.async_fire(EVENT_PRESS, {"address": address, "type": event_type, **data})

    @callback
    def schedule_reads(self, bus_address: str, *, released: bool) -> None:
        """Read the output groups a key drives, once they have settled.

        A relay has switched 0.3 s after the first frame, unless the key
        is still held (a pushbutton link), and is read again after the
        release. A dimmer moves while the key is held and for a moment
        after it: it is read after the release only.
        """
        for address, group in self._hub.inventory.impacted_groups(bus_address):
            module_type = self._hub.inventory.module_type(address)
            if module_type not in (*OUTPUT_MODULE_TYPES, RGB_MODULE):
                continue
            dimmer = module_type == DIMMER_MODULE
            if dimmer and not released:
                continue
            if released:
                self.schedule_read(address, group, DIMMER_READ_DELAY if dimmer else RELEASE_READ_DELAY)
            else:
                self.schedule_read(address, group, PRESS_READ_DELAY, unless_held=bus_address)

    @callback
    def schedule_read(
        self, address: str, group: int, delay: float, *, unless_held: str | None = None
    ) -> None:
        """Read one group after ``delay``; a newer request for it replaces this one."""
        if (pending := self._reads.get((address, group))) is not None:
            pending.cancel()
        self._reads[(address, group)] = self._hub.entry.async_create_background_task(
            self._hub.hass,
            self._async_read(address, group, delay, unless_held),
            name=f"{DOMAIN}_read_{address}_{group}",
        )

    async def _async_read(
        self, address: str, group: int, delay: float, unless_held: str | None
    ) -> None:
        await asyncio.sleep(delay)
        if self._reads.get((address, group)) is asyncio.current_task():
            del self._reads[(address, group)]
        if unless_held is not None and unless_held in self.tracker:
            return
        if self._hub.connected and not self._hub.busy:
            await self._hub.async_read_group(address, group)
