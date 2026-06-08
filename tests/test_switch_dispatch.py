"""The switch platform wakes only the impacted module's entities on a press.

Before: every relay/cover switch subscribed to the shared
``EVENT_BUTTON_OPERATION`` bus event, so one press invoked **all N**
entity callbacks, each filtering itself out by address — O(N) per press,
growing with the whole install.

After: each switch connects to a per-address dispatcher signal
(``operation_signal(address)``), so a press wakes only that module's
channels — O(impacted), independent of N. These tests pin that routing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from custom_components.nikobus.const import operation_signal
from custom_components.nikobus.switch import NikobusRelaySwitchEntity


class _Dispatcher:
    """Faithful stand-in for HA's dispatcher: connections are keyed by the
    exact signal string, and ``send`` invokes only that signal's callbacks
    — the property that makes routing O(impacted) instead of O(N)."""

    def __init__(self) -> None:
        self.by_signal: dict[str, list] = {}

    def connect(self, _hass, signal, cb):
        self.by_signal.setdefault(signal, []).append(cb)
        return lambda: self.by_signal[signal].remove(cb)

    def send(self, _hass, signal, *args):
        for cb in list(self.by_signal.get(signal, [])):
            cb(*args)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_switch(coord, hass, addr, channel):
    e = NikobusRelaySwitchEntity(coord, addr, channel, f"ch{channel}", "Mod", "05-002")
    e.hass = hass
    e.async_write_ha_state = MagicMock()
    return e


def _wire(dispatcher, hass, addrs, channels=(1, 2, 3)):
    """Build + register a switch per (module, channel); return the list."""
    coord = MagicMock()
    switches = []
    with patch(
        "custom_components.nikobus.switch.async_dispatcher_connect", dispatcher.connect
    ):
        for addr in addrs:
            for ch in channels:
                e = _make_switch(coord, hass, addr, ch)
                _run(e.async_added_to_hass())
                switches.append(e)
    return switches


@pytest.mark.parametrize("n_modules", [2, 5, 20], ids=lambda n: f"{n}modules")
def test_operation_wakes_only_impacted_module(n_modules):
    disp = _Dispatcher()
    hass = MagicMock()
    addrs = [f"{i:04X}" for i in range(1, n_modules + 1)]
    switches = _wire(disp, hass, addrs)

    # No switch registers a listener on the shared event bus anymore.
    hass.bus.async_listen.assert_not_called()

    # A press impacts one module — notify only that module's signal.
    target = addrs[0]
    disp.send(hass, operation_signal(target))

    woken = [e for e in switches if e.async_write_ha_state.called]
    assert {e._address for e in woken} == {target}
    # Exactly the impacted module's channels woke — regardless of how many
    # modules (N) exist on the bus.
    assert len(woken) == 3
    assert len(switches) == 3 * n_modules


def test_connections_are_partitioned_by_address():
    """Each switch registers exactly one per-address connection, and the
    connections partition by signal — so no single ``send`` can reach all
    of them (the structural guarantee a shared event_type lacks)."""
    disp = _Dispatcher()
    switches = _wire(disp, MagicMock(), ("AAAA", "BBBB"))

    total = sum(len(cbs) for cbs in disp.by_signal.values())
    assert total == len(switches) == 6
    # Two distinct signals, 3 callbacks each — a send reaches at most 3.
    assert sorted(len(cbs) for cbs in disp.by_signal.values()) == [3, 3]
    assert set(disp.by_signal) == {
        operation_signal("AAAA"),
        operation_signal("BBBB"),
    }


def test_handler_drops_optimistic_state():
    """The per-address handler clears optimistic state and writes once."""
    disp = _Dispatcher()
    switch = _wire(disp, MagicMock(), ("0E6C",), channels=(1,))[0]
    switch._is_on = True

    disp.send(MagicMock(), operation_signal("0E6C"))

    assert switch._is_on is None
    switch.async_write_ha_state.assert_called_once()
