"""Coordinator updates skip the HA-state write when nothing visible changed.

Each output module is polled every cycle and its channels are woken (via
the coordinator listener *and* the per-module signal). Without diffing,
every channel re-rendered and recomputed its attributes each cycle even
when its byte was unchanged. ``NikobusEntity._handle_coordinator_update``
now diffs on ``(available, render_state)`` and writes only on a real
change — these pin that for switch (on/off) and dimmer (on/off + level).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.nikobus.light import NikobusDimmerEntity
from custom_components.nikobus.switch import NikobusRelaySwitchEntity


def _switch(state=False):
    coord = MagicMock()
    coord.get_switch_state = MagicMock(return_value=state)
    coord.nikobus_connection.is_connected = True
    e = NikobusRelaySwitchEntity(coord, "3851", 3, "Relay", "Switch", "05-002")
    e.async_write_ha_state = MagicMock()
    return e, coord


def _dimmer(level=0):
    coord = MagicMock()
    coord.get_light_brightness = MagicMock(return_value=level)
    coord.nikobus_connection.is_connected = True
    e = NikobusDimmerEntity(coord, "0E6C", 1, "Lamp", "Dimmer", "05-007")
    e.async_write_ha_state = MagicMock()
    return e, coord


def test_unchanged_updates_skip_the_write():
    e, coord = _switch(state=False)
    e._handle_coordinator_update()           # first render: None -> off, writes
    e._handle_coordinator_update()           # identical: skipped
    e._handle_coordinator_update()           # identical: skipped
    assert e.async_write_ha_state.call_count == 1

    coord.get_switch_state.return_value = True
    e._handle_coordinator_update()           # off -> on: writes
    assert e.async_write_ha_state.call_count == 2


def test_availability_flip_writes_even_if_state_same():
    e, coord = _switch(state=True)
    e._handle_coordinator_update()           # writes (available, on)
    coord.nikobus_connection.is_connected = False
    e._handle_coordinator_update()           # now unavailable: writes
    assert e.async_write_ha_state.call_count == 2


def test_update_clears_optimistic_state_before_diffing():
    e, coord = _switch(state=False)
    e._is_on = True                          # stale optimistic 'on'
    e._handle_coordinator_update()           # cleared, real state is off
    assert e._is_on is None
    assert e.is_on is False


def test_dimmer_diffs_on_brightness():
    e, coord = _dimmer(level=100)
    e._handle_coordinator_update()           # writes
    e._handle_coordinator_update()           # same level: skipped
    assert e.async_write_ha_state.call_count == 1

    coord.get_light_brightness.return_value = 200
    e._handle_coordinator_update()           # brightness changed: writes
    assert e.async_write_ha_state.call_count == 2


def test_dimmer_update_clears_both_optimistic_caches():
    e, coord = _dimmer(level=0)
    e._is_on = True
    e._optimistic_brightness = 180
    e._handle_coordinator_update()
    assert e._is_on is None
    assert e._optimistic_brightness is None
