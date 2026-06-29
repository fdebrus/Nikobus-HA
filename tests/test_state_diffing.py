"""Coordinator updates skip the HA-state write when nothing visible changed.

Each output module is polled every cycle and its channels are woken (via
the coordinator listener *and* the per-module signal). Without diffing,
every channel re-rendered and recomputed its attributes each cycle even
when its byte was unchanged. ``NikobusEntity._handle_coordinator_update``
now diffs on ``(available, render_state)`` and writes only on a real
change — these pin that for switch (on/off) and dimmer (on/off + level).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# Regression: issue #469
#
# The write-diff cache (``_last_render``) is only meaningful if it tracks
# what's *actually displayed*, not just the last coordinator-driven render.
# Optimistic writes (``async_turn_on`` / ``async_turn_off``) and the
# button-operation handler move the displayed state via
# ``async_write_ha_state`` — those must refresh the cache too. Otherwise a
# later coordinator update that renders the same value as the stale cache
# (e.g. the real OFF state after an optimistic ON) is wrongly suppressed,
# freezing the entity on the optimistic value. ``NikobusEntity`` overrides
# ``async_write_ha_state`` to keep the cache honest; these pin that.
#
# We patch only the underlying HA write so the entity's own override runs
# (the other tests above replace ``async_write_ha_state`` wholesale, which
# would bypass exactly the code under test here).
# ---------------------------------------------------------------------------


def test_switch_optimistic_write_unfreezes_later_real_state_issue_469():
    e, coord = _switch(state=False)
    del e.async_write_ha_state  # restore the real override (helper stubbed it)
    with patch(
        "homeassistant.helpers.update_coordinator."
        "CoordinatorEntity.async_write_ha_state"
    ) as raw_write:
        # 1. Steady OFF — coordinator render writes; cache = (True, off).
        e._handle_coordinator_update()
        assert raw_write.call_count == 1

        # 2. User turns it ON from HA — optimistic displayed state. The
        #    coordinator byte hasn't changed, but the cache must follow.
        e._is_on = True
        e.async_write_ha_state()
        assert raw_write.call_count == 2

        # 3. Physical OFF — coordinator now reads OFF. Pre-fix the cache was
        #    stale at (True, off) and this write was dropped, leaving the
        #    entity stuck on the optimistic ON (issue #469).
        coord.get_switch_state.return_value = False
        e._handle_coordinator_update()
        assert raw_write.call_count == 3


def test_dimmer_optimistic_write_unfreezes_later_real_state_issue_469():
    e, coord = _dimmer(level=0)
    del e.async_write_ha_state
    with patch(
        "homeassistant.helpers.update_coordinator."
        "CoordinatorEntity.async_write_ha_state"
    ) as raw_write:
        # 1. Steady OFF — writes; cache = (True, (off, 0)).
        e._handle_coordinator_update()
        assert raw_write.call_count == 1

        # 2. Optimistic ON @255 — cache must track the displayed brightness.
        e._is_on = True
        e._optimistic_brightness = 255
        e.async_write_ha_state()
        assert raw_write.call_count == 2

        # 3. Physical OFF — coordinator reads 0; the OFF write must land.
        coord.get_light_brightness.return_value = 0
        e._handle_coordinator_update()
        assert raw_write.call_count == 3
