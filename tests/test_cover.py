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
    NikobusCFCoverEntity,
    NikobusCoverEntity,
    _parse_cf_time,
    _parse_operation_time,
    STATE_STOPPED,
    STATE_OPENING,
    STATE_CLOSING,
    STATE_ERROR,
)
from custom_components.nikobus.nkbtravelcalculator import NikobusTravelCalculator

_MONO = "custom_components.nikobus.nkbtravelcalculator.time.monotonic"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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


class TestActionErrors(unittest.TestCase):
    """A failed bus command surfaces as a translated HomeAssistantError."""

    def test_open_translates_bus_error(self):
        from homeassistant.exceptions import HomeAssistantError

        ent, coord = _make_cover()
        ent._state = STATE_STOPPED
        coord.api.open_cover = AsyncMock(side_effect=RuntimeError("bus down"))
        with self.assertRaises(HomeAssistantError) as cm:
            _run(ent.async_open_cover())
        self.assertEqual(cm.exception.translation_key, "communication_error")
        self.assertIsInstance(cm.exception.__cause__, RuntimeError)

    def test_stop_translates_bus_error(self):
        from homeassistant.exceptions import HomeAssistantError

        ent, coord = _make_cover()
        ent._state = STATE_OPENING
        coord.api.stop_cover = AsyncMock(side_effect=RuntimeError("bus down"))
        with self.assertRaises(HomeAssistantError) as cm:
            _run(ent.async_stop_cover())
        self.assertEqual(cm.exception.translation_key, "communication_error")


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

    def test_idle_unchanged_poll_skips_write(self):
        # An idle cover polled with the same bus state writes once, then
        # diffs out the redundant re-renders.
        ent, _ = self._setup(STATE_STOPPED, state=STATE_STOPPED)
        ent.async_write_ha_state = MagicMock()
        ent._handle_coordinator_update()
        ent._handle_coordinator_update()
        ent._handle_coordinator_update()
        self.assertEqual(ent.async_write_ha_state.call_count, 1)

    def test_position_change_writes(self):
        ent, _ = self._setup(STATE_STOPPED, state=STATE_STOPPED)
        ent.async_write_ha_state = MagicMock()
        ent._handle_coordinator_update()           # initial render
        ent._position = ent._position + 25         # e.g. motion advanced it
        ent._handle_coordinator_update()           # position changed -> writes
        self.assertEqual(ent.async_write_ha_state.call_count, 2)


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


# ---------------------------------------------------------------------------
# Regressions from the cover.py correctness audit
# ---------------------------------------------------------------------------
class TestStopFromMotionLoopSendsBusStop(unittest.TestCase):
    def test_self_cancel_does_not_swallow_stop_command(self):
        """BUG 1: _stop() called FROM the motion task must still deliver
        the bus STOP. The old code cancelled its own task; CancelledError
        was injected at the next await — the api.stop_cover call itself —
        and swallowed by the loop, so the physical shutter kept moving.
        AsyncMock has no suspension point and masked this; this stub
        suspends for real."""

        async def scenario():
            ent, coord = _make_cover()
            sent = []

            async def real_stop(*args):
                await asyncio.sleep(0)  # genuine suspension point
                sent.append(args)

            coord.api.stop_cover = real_stop
            ent._state = STATE_CLOSING

            async def fake_motion_loop():
                # Mirrors _motion_loop's structure: stop from within the
                # task registered as _motion_task, CancelledError swallowed.
                try:
                    await ent._stop(send_stop=True)
                except asyncio.CancelledError:
                    pass

            task = asyncio.get_event_loop().create_task(fake_motion_loop())
            ent._motion_task = task
            await asyncio.sleep(0.05)
            return sent, task

        sent, task = _run(scenario())
        self.assertEqual(len(sent), 1, "bus STOP was swallowed by self-cancel")
        self.assertTrue(task.done() and not task.cancelled())


class TestRetargetSameDirection(unittest.TestCase):
    def test_start_travel_anchors_on_in_flight_position(self):
        """BUG 2: re-targeting mid-travel in the same direction must not
        snap the simulated position back to the stale committed value."""
        c = NikobusTravelCalculator(10, 10)
        c.set_position(100)
        clock = {"t": 1000.0}
        with patch(_MONO, lambda: clock["t"]):
            c.start_travel("closing")
            clock["t"] = 1005.0  # 5 s of 10 s → position 50
            self.assertEqual(c.current_position(), 50.0)
            c.start_travel("closing")  # re-target while moving
            self.assertEqual(c.current_position(), 50.0)  # not 100
            clock["t"] = 1007.0  # 2 more seconds → 30
            self.assertEqual(c.current_position(), 30.0)


class TestUnresolvedChannelPress(unittest.TestCase):
    def test_press_without_channel_does_not_stop_moving_cover(self):
        """D3: a press payload with channel=None (undecoded button link)
        reaches every cover on the module — it must not stop one that is
        moving (that desyncs the simulated position from the shutter)."""
        ent, coord = _make_cover()
        ent._state = STATE_CLOSING
        ent._movement_source = "ha"
        _run(ent._handle_button_pressed({"address": "9105", "channel": None}))
        self.assertEqual(ent._state, STATE_CLOSING)  # untouched
        coord.api.stop_cover and self.assertFalse(
            getattr(coord.api.stop_cover, "await_count", 0)
        )

    def test_press_with_matching_channel_still_stops(self):
        ent, coord = _make_cover()
        ent._state = STATE_CLOSING
        ent._movement_source = "ha"
        _run(ent._handle_button_pressed({"address": "9105", "channel": 1}))
        self.assertEqual(ent._state, STATE_STOPPED)
        coord.api.stop_cover.assert_awaited_once()


class TestErrorClearEpisode(unittest.TestCase):
    def _ent_with_bus_states(self, states):
        ent, coord = _make_cover()
        coord.get_cover_state = MagicMock(side_effect=states)
        ent._movement_source = "ha"
        return ent, coord

    def test_clear_sent_once_per_error_episode(self):
        """D4: repeated polls while 0x03 persists must not re-send the
        protection clear; leaving 0x03 re-arms it for the next episode."""
        ent, _ = self._ent_with_bus_states(
            [STATE_ERROR, STATE_ERROR, STATE_STOPPED, STATE_ERROR]
        )
        ent._handle_coordinator_update()  # episode 1 → clear
        ent._handle_coordinator_update()  # still 0x03 → suppressed
        self.assertEqual(ent.hass.async_create_task.call_count, 1)
        ent._handle_coordinator_update()  # bus left 0x03 → re-arm
        ent._handle_coordinator_update()  # episode 2 → clear again
        self.assertEqual(ent.hass.async_create_task.call_count, 2)

    def test_protection_clear_uses_last_motion_direction(self):
        """D4: the force_api stop fires after _state is already STOPPED —
        it must use the direction the motion actually ran in, not an
        arbitrary 'closing'."""
        ent, coord = _make_cover()
        ent._state = STATE_STOPPED
        ent._last_motion_direction = "opening"
        _run(ent._stop(send_stop=True, force_api=True))
        coord.api.stop_cover.assert_awaited_once_with("9105", 1, "opening")


# ---------------------------------------------------------------------------
# _parse_cf_time — CF member timing strings → seconds
# ---------------------------------------------------------------------------
class TestParseCfTime(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(_parse_cf_time("40 s"), 40.0)
        self.assertEqual(_parse_cf_time("30s"), 30.0)

    def test_minutes(self):
        self.assertEqual(_parse_cf_time("2 m"), 120.0)

    def test_none_and_unparseable(self):
        self.assertIsNone(_parse_cf_time(None))
        self.assertIsNone(_parse_cf_time(""))
        self.assertIsNone(_parse_cf_time("abc"))

    def test_non_positive(self):
        self.assertIsNone(_parse_cf_time("0 s"))
        self.assertIsNone(_parse_cf_time("-5 s"))


# ---------------------------------------------------------------------------
# NikobusCFCoverEntity — grouped 2-button roller central function
# ---------------------------------------------------------------------------
def _make_cf_cover(members=None, modules_channels=6):
    """A CF cover over module 8CF5 channels 1,2,3 by default."""
    if members is None:
        members = [
            {"module_address": "8CF5", "channel": 1, "open_time": "40 s", "close_time": "40 s"},
            {"module_address": "8CF5", "channel": 2, "open_time": "30 s", "close_time": "30 s"},
            {"module_address": "8CF5", "channel": 3, "open_time": "30 s", "close_time": "30 s"},
        ]
    coord = MagicMock()
    coord.get_cover_operation_time = MagicMock(return_value=30.0)
    coord.get_module_channel_count = MagicMock(return_value=modules_channels)
    coord.nikobus_module_states = {}
    coord.set_bytearray_group_state = MagicMock()
    coord.api.set_output_states_for_module = AsyncMock()
    coord.async_event_handler = AsyncMock()
    coord.address_label = MagicMock(side_effect=lambda a: f"name({a})")
    cf_config = {"pattern": "roller_pair", "outputs": []}
    ent = NikobusCFCoverEntity(coord, "3880cd", cf_config, members)
    ent.hass = MagicMock()

    def _fake_create_task(arg):
        if asyncio.iscoroutine(arg):
            arg.close()
        return MagicMock()

    ent.hass.async_create_task = MagicMock(side_effect=_fake_create_task)
    ent.async_write_ha_state = MagicMock()
    return ent, coord


class TestCFCoverConstruction(unittest.TestCase):
    def test_unique_id_and_calculator_seeded_with_max(self):
        ent, _ = _make_cf_cover()
        self.assertEqual(ent._attr_unique_id, "nikobus_cf_cover_3880cd")
        self.assertEqual(ent._bus_address, "3880CD")
        # Slowest member (40 s) seeds the group travel model.
        self.assertEqual(ent._calculator.time_up, 40.0)
        self.assertEqual(ent._calculator.time_down, 40.0)
        self.assertEqual(len(ent._members), 3)

    def test_unparseable_time_falls_back_to_module_config(self):
        members = [{"module_address": "8CF5", "channel": 1,
                    "open_time": None, "close_time": None}]
        ent, coord = _make_cf_cover(members=members)
        # _parse_cf_time(None) → coordinator.get_cover_operation_time (30.0)
        self.assertEqual(ent._members[0]["open_time"], 30.0)
        self.assertEqual(ent._members[0]["close_time"], 30.0)


class TestCFCoverMove(unittest.TestCase):
    def test_close_commits_once_per_module_atomically(self):
        ent, coord = _make_cf_cover()
        _run(ent._move("closing"))
        # All three channels are on one module → exactly one bus commit.
        coord.api.set_output_states_for_module.assert_awaited_once_with(address="8CF5")
        # Group-1 state staged with channels 1,2,3 = CLOSING (0x02).
        group1_hex = coord.set_bytearray_group_state.call_args_list[0].args[2]
        self.assertEqual(group1_hex[:6], "020202")
        self.assertEqual(ent._state, STATE_CLOSING)

    def test_open_stages_opening_byte(self):
        ent, coord = _make_cf_cover()
        _run(ent._move("opening"))
        group1_hex = coord.set_bytearray_group_state.call_args_list[0].args[2]
        self.assertEqual(group1_hex[:6], "010101")  # 0x01 = opening
        self.assertEqual(ent._state, STATE_OPENING)

    def test_cross_module_commits_once_per_module(self):
        members = [
            {"module_address": "8CF5", "channel": 1, "open_time": "40 s", "close_time": "40 s"},
            {"module_address": "9105", "channel": 2, "open_time": "30 s", "close_time": "30 s"},
        ]
        ent, coord = _make_cf_cover(members=members)
        _run(ent._move("closing"))
        self.assertEqual(coord.api.set_output_states_for_module.await_count, 2)
        addrs = {c.kwargs["address"] for c in coord.api.set_output_states_for_module.await_args_list}
        self.assertEqual(addrs, {"8CF5", "9105"})

    def test_move_schedules_timed_stops(self):
        ent, coord = _make_cf_cover()
        _run(ent._move("closing"))
        # One module → one timed-stop task (plus the motion loop task).
        self.assertEqual(len(ent._module_tokens), 1)
        self.assertIn("8CF5", ent._module_tokens)


class TestCFCoverStop(unittest.TestCase):
    def test_stop_commits_stopped_and_cancels_pending(self):
        ent, coord = _make_cf_cover()
        # Pretend a move is in flight with a pending stop task.
        pending = MagicMock()
        ent._stop_tasks = [pending]
        ent._module_tokens = {"8CF5": "tok"}
        _run(ent._stop())
        pending.cancel.assert_called_once()
        self.assertEqual(ent._module_tokens, {})
        self.assertEqual(ent._state, STATE_STOPPED)
        # Stopped byte (0x00) staged for the member channels.
        group1_hex = coord.set_bytearray_group_state.call_args_list[-1].args[2]
        self.assertEqual(group1_hex[:6], "000000")
        coord.api.set_output_states_for_module.assert_awaited_with(address="8CF5")


class TestCFCoverActionErrors(unittest.TestCase):
    def test_close_translates_bus_error(self):
        from homeassistant.exceptions import HomeAssistantError

        ent, coord = _make_cf_cover()
        coord.api.set_output_states_for_module = AsyncMock(
            side_effect=RuntimeError("bus down")
        )
        with self.assertRaises(HomeAssistantError) as cm:
            _run(ent.async_close_cover())
        self.assertEqual(cm.exception.translation_key, "communication_error")


class TestCFCoverTimedStop(unittest.TestCase):
    def test_delayed_stop_only_touches_unchanged_channels(self):
        """A channel redirected during travel (its byte no longer matches
        what we sent) is left alone by the timed stop."""
        ent, coord = _make_cf_cover()
        sent = bytearray(12)
        sent[0] = STATE_CLOSING  # ch1 we sent closing
        sent[1] = STATE_CLOSING  # ch2 we sent closing
        # Current state: ch1 still closing (ours), ch2 reversed to opening.
        current = bytearray(12)
        current[0] = STATE_CLOSING
        current[1] = STATE_OPENING
        coord.nikobus_module_states = {"8CF5": current}
        ent._module_tokens = {"8CF5": "tok"}
        with patch("custom_components.nikobus.cover.asyncio.sleep", new=AsyncMock()):
            _run(ent._delayed_stop("8CF5", sent, {0, 1}, 0.0, "tok"))
        # Committed stop state: ch1 → stopped, ch2 untouched (still opening).
        group1_hex = coord.set_bytearray_group_state.call_args_list[-1].args[2]
        self.assertEqual(group1_hex[:4], "0001")  # 00=ch1 stopped, 01=ch2 left opening

    def test_delayed_stop_aborted_when_token_superseded(self):
        ent, coord = _make_cf_cover()
        ent._module_tokens = {"8CF5": "newtok"}
        sent = bytearray(12)
        with patch("custom_components.nikobus.cover.asyncio.sleep", new=AsyncMock()):
            _run(ent._delayed_stop("8CF5", sent, {0}, 0.0, "oldtok"))
        coord.api.set_output_states_for_module.assert_not_awaited()
