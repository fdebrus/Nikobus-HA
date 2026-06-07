"""Characterization tests for the Nikobus cover platform.

cover.py is the integration's most complex, stateful code (time-based
position dead-reckoning, motor-protection handling, HA-vs-Nikobus move
sourcing) and previously had no direct test coverage. These tests pin
the current behavior so the file can be refactored safely later. They
deliberately avoid the live 0.5s motion loop (timing-flaky) and instead
exercise the calculator, the pure helpers, the properties, and the
state-machine transitions with the scheduled tasks mocked out.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.nikobus.cover import (
    NikobusCoverEntity,
    _parse_operation_time,
    STATE_STOPPED,
    STATE_OPENING,
    STATE_CLOSING,
    STATE_ERROR,
)
from custom_components.nikobus.nkbtravelcalculator import NikobusTravelCalculator

_MONO = "custom_components.nikobus.nkbtravelcalculator.time.monotonic"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# NikobusTravelCalculator — pure position math
# ---------------------------------------------------------------------------
class TestTravelCalculator(unittest.TestCase):
    def test_set_position_clamps(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(150)
        self.assertEqual(c.position, 100.0)
        c.set_position(-5)
        self.assertEqual(c.position, 0.0)

    def test_opening_progress(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(0)
        clock = {"t": 1000.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("opening")
            clock["t"] = 1005.0  # 5s of a 10s up-travel = +50%
            self.assertEqual(c.current_position(), 50.0)

    def test_closing_progress(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(100)
        clock = {"t": 2000.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("closing")
            clock["t"] = 2003.0  # 3s of a 10s down-travel = -30%
            self.assertEqual(c.current_position(), 70.0)

    def test_opening_clamps_at_100(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(80)
        clock = {"t": 0.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("opening")
            clock["t"] = 5.0  # +50% from 80 -> 130 -> clamp 100
            self.assertEqual(c.current_position(), 100.0)

    def test_closing_clamps_at_0(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(20)
        clock = {"t": 0.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("closing")
            clock["t"] = 5.0  # -50% from 20 -> -30 -> clamp 0
            self.assertEqual(c.current_position(), 0.0)

    def test_latency_backdates_start(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(0)
        clock = {"t": 1000.0}
        with patch(_MONO, lambda: clock["t"]):
            # 2s already elapsed before we noticed -> immediately at 20%.
            c.start_travel("opening", latency=2.0)
            self.assertEqual(c.current_position(), 20.0)

    def test_stop_locks_position_and_clears_direction(self):
        c = NikobusTravelCalculator(10, 10)
        c.set_position(0)
        clock = {"t": 1000.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("opening")
            clock["t"] = 1005.0
            c.stop()
            self.assertEqual(c.position, 50.0)
            clock["t"] = 9999.0  # time moves on, but we're stopped
            self.assertEqual(c.current_position(), 50.0)

    def test_zero_active_time_guard(self):
        c = NikobusTravelCalculator(0, 0)
        c.set_position(50)
        clock = {"t": 0.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("opening")
            clock["t"] = 100.0
            self.assertEqual(c.current_position(), 50.0)


# ---------------------------------------------------------------------------
# _parse_operation_time
# ---------------------------------------------------------------------------
class TestParseOperationTime(unittest.TestCase):
    def test_none_uses_fallback_silently(self):
        self.assertEqual(_parse_operation_time(None, 30.0, "t", "ADDR"), 30.0)

    def test_valid_positive(self):
        self.assertEqual(_parse_operation_time("12.5", 30.0, "t", "ADDR"), 12.5)

    def test_zero_and_negative_fall_back(self):
        self.assertEqual(_parse_operation_time(0, 30.0, "t", "ADDR"), 30.0)
        self.assertEqual(_parse_operation_time(-4, 30.0, "t", "ADDR"), 30.0)

    def test_non_numeric_falls_back(self):
        self.assertEqual(_parse_operation_time("abc", 30.0, "t", "ADDR"), 30.0)


# ---------------------------------------------------------------------------
# NikobusCoverEntity — state machine
# ---------------------------------------------------------------------------
def _make_cover(op_up=10.0, op_down=10.0):
    coord = MagicMock()
    coord.api.open_cover = AsyncMock()
    coord.api.close_cover = AsyncMock()
    coord.api.stop_cover = AsyncMock()
    coord.set_bytearray_state = MagicMock()
    ent = NikobusCoverEntity(
        coord, "9105", 1, "Shutter 1", "Roller module", "05-001", op_up, op_down
    )
    ent.hass = MagicMock()

    # async_create_task must not leak un-awaited coroutines.
    def _fake_create_task(arg):
        if asyncio.iscoroutine(arg):
            arg.close()
        return MagicMock()

    ent.hass.async_create_task = MagicMock(side_effect=_fake_create_task)
    ent.async_write_ha_state = MagicMock()
    return ent, coord


class TestCoverProperties(unittest.TestCase):
    def test_position_rounds(self):
        ent, _ = _make_cover()
        ent._position = 42.6
        self.assertEqual(ent.current_cover_position, 43)

    def test_is_opening_closing_open_closed(self):
        ent, _ = _make_cover()
        ent._state, ent._position = STATE_OPENING, 50.0
        self.assertTrue(ent.is_opening)
        self.assertFalse(ent.is_closing)
        ent._state, ent._position = STATE_CLOSING, 50.0
        self.assertTrue(ent.is_closing)
        ent._position = 0.0
        self.assertTrue(ent.is_closed)
        self.assertFalse(ent.is_closing)  # position 0 → not "closing" anymore
        ent._position = 100.0
        self.assertTrue(ent.is_open)


class TestShouldStop(unittest.TestCase):
    def test_opening_reaches_target(self):
        ent, _ = _make_cover()
        ent._state = STATE_OPENING
        ent._target_position = 50
        ent._position = 49.0
        self.assertFalse(ent._should_stop())
        ent._position = 50.0
        self.assertTrue(ent._should_stop())

    def test_closing_reaches_target(self):
        ent, _ = _make_cover()
        ent._state = STATE_CLOSING
        ent._target_position = 50
        ent._position = 51.0
        self.assertFalse(ent._should_stop())
        ent._position = 50.0
        self.assertTrue(ent._should_stop())

    def test_no_target_never_stops(self):
        ent, _ = _make_cover()
        ent._state = STATE_OPENING
        ent._target_position = None
        ent._position = 100.0
        self.assertFalse(ent._should_stop())


class TestStop(unittest.TestCase):
    def test_finalizes_state_and_optimistic_write(self):
        ent, coord = _make_cover()
        ent._state = STATE_OPENING
        ent._target_position = 50
        _run(ent._stop(send_stop=False))
        self.assertEqual(ent._state, STATE_STOPPED)
        self.assertIsNone(ent._target_position)
        coord.set_bytearray_state.assert_called_once_with("9105", 1, STATE_STOPPED)
        coord.api.stop_cover.assert_not_awaited()

    def test_send_stop_while_moving_calls_api(self):
        ent, coord = _make_cover()
        ent._state = STATE_OPENING
        _run(ent._stop(send_stop=True))
        coord.api.stop_cover.assert_awaited_once_with("9105", 1, "opening")

    def test_send_stop_when_already_stopped_skips_api(self):
        ent, coord = _make_cover()
        ent._state = STATE_STOPPED
        _run(ent._stop(send_stop=True))
        coord.api.stop_cover.assert_not_awaited()

    def test_force_api_sends_even_when_stopped(self):
        ent, coord = _make_cover()
        ent._state = STATE_STOPPED
        _run(ent._stop(send_stop=True, force_api=True))
        coord.api.stop_cover.assert_awaited_once()


class TestHandleButtonPressed(unittest.TestCase):
    # The signal is routed by module address, so the handler only filters
    # by channel (one roller module carries several covers).

    def test_subscribes_to_its_module_press_signal(self):
        from custom_components.nikobus.const import press_signal

        ent, _ = _make_cover()
        with patch(
            "custom_components.nikobus.cover.async_dispatcher_connect",
            return_value=lambda: None,
        ) as conn:
            _run(ent.async_added_to_hass())
        signals = [c.args[1] for c in conn.call_args_list]
        self.assertIn(press_signal("9105"), signals)

    def test_ignores_other_channel(self):
        ent, _ = _make_cover()
        ent._state = STATE_STOPPED
        _run(ent._handle_button_pressed({"channel": 2}))
        self.assertIsNone(ent._last_button_press_monotonic)

    def test_records_timestamp_when_stopped(self):
        ent, _ = _make_cover()
        ent._state = STATE_STOPPED
        _run(ent._handle_button_pressed({"channel": 1}))
        self.assertIsNotNone(ent._last_button_press_monotonic)

    def test_press_while_moving_is_a_stop(self):
        ent, _ = _make_cover()
        ent._state = STATE_OPENING
        ent._movement_source = "ha"
        ent._stop = AsyncMock()
        _run(ent._handle_button_pressed({"channel": 1}))
        ent._stop.assert_awaited_once_with(send_stop=True)


class TestHandleCoordinatorUpdate(unittest.TestCase):
    def _setup(self, bus_state, *, state=STATE_STOPPED, source="ha"):
        ent, coord = _make_cover()
        ent._state = state
        ent._movement_source = source
        coord.get_cover_state = MagicMock(return_value=bus_state)
        ent._stop = MagicMock()
        ent._start_motion_logic = MagicMock()
        return ent, coord

    def test_same_state_is_noop(self):
        ent, _ = self._setup(STATE_STOPPED, state=STATE_STOPPED)
        ent._handle_coordinator_update()
        ent._stop.assert_not_called()
        ent._start_motion_logic.assert_not_called()

    def test_error_after_ha_move_sends_active_stop(self):
        ent, _ = self._setup(STATE_ERROR, state=STATE_OPENING, source="ha")
        ent._handle_coordinator_update()
        ent._stop.assert_called_once_with(send_stop=True, force_api=True)

    def test_error_after_nikobus_move_waits_and_schedules_recovery(self):
        ent, _ = self._setup(STATE_ERROR, state=STATE_OPENING, source="nikobus")
        ent._handle_coordinator_update()
        ent._stop.assert_called_once_with(send_stop=False)
        # one task for the stop, one for the deferred error-clear refresh
        self.assertEqual(ent.hass.async_create_task.call_count, 2)

    def test_bus_stopped_triggers_stop(self):
        ent, _ = self._setup(STATE_STOPPED, state=STATE_OPENING)
        ent._handle_coordinator_update()
        ent._stop.assert_called_once_with(send_stop=False)

    def test_bus_moving_starts_motion_as_nikobus(self):
        ent, _ = self._setup(STATE_OPENING, state=STATE_STOPPED)
        ent._handle_coordinator_update()
        ent._start_motion_logic.assert_called_once()
        self.assertEqual(ent._start_motion_logic.call_args.args[0], "opening")
        self.assertEqual(ent._movement_source, "nikobus")


class TestSetCoverPosition(unittest.TestCase):
    def test_noop_when_already_at_target(self):
        ent, _ = _make_cover()
        ent._position = 50.0
        _run(ent.async_set_cover_position(position=50))
        ent.hass.async_create_task.assert_not_called()

    def test_schedules_debounced_move(self):
        ent, _ = _make_cover()
        ent._position = 0.0
        _run(ent.async_set_cover_position(position=60))
        ent.hass.async_create_task.assert_called_once()

    def test_cancels_prior_coalesce_task(self):
        ent, _ = _make_cover()
        ent._position = 0.0
        prior = MagicMock()
        ent._coalesce_task = prior
        _run(ent.async_set_cover_position(position=60))
        prior.cancel.assert_called_once()


class TestRequestMove(unittest.TestCase):
    def test_open_from_stopped_calls_api(self):
        ent, coord = _make_cover()
        ent._state = STATE_STOPPED
        _run(ent._request_move("opening"))
        self.assertEqual(ent._movement_source, "ha")
        coord.api.open_cover.assert_awaited_once()
        self.assertEqual(coord.api.open_cover.await_args.args[:2], ("9105", 1))

    def test_reverse_stops_first(self):
        ent, coord = _make_cover()
        ent._state = STATE_OPENING  # moving up; request down → must stop first
        ent._stop = AsyncMock()
        with patch("custom_components.nikobus.cover.asyncio.sleep", new=AsyncMock()):
            _run(ent._request_move("closing"))
        ent._stop.assert_awaited_once_with(send_stop=True)
        coord.api.close_cover.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
