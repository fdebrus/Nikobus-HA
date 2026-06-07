"""Characterization tests for the button binary sensor.

Event-driven: a matching bus press flips it to 'pressed' and schedules a
reset back to 'idle'. Pins the address match + reset-timer behavior.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from custom_components.nikobus.binary_sensor import NikobusButtonBinarySensor
from custom_components.nikobus.const import press_signal


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make():
    coord = MagicMock()
    e = NikobusButtonBinarySensor(
        coord, "1A2B3C", "1A", {"bus_address": "081032"}, parent_phys=None
    )
    e.hass = MagicMock()
    return e, coord


class TestButtonBinarySensor(unittest.TestCase):
    def test_initial_state_idle(self):
        e, _ = _make()
        self.assertFalse(e._attr_is_on)
        self.assertEqual(e.state, "idle")

    def test_press_sets_pressed_and_schedules_reset(self):
        # The signal is per-address, so any delivery is this button's press.
        e, _ = _make()
        e._handle_button_event({"address": "081032"})
        self.assertTrue(e._attr_is_on)
        self.assertEqual(e.state, "pressed")
        # async_call_later (stubbed) returns a cancel handle
        self.assertIsNotNone(e._reset_timer_cancel)

    def test_subscribes_to_its_own_press_signal(self):
        e, _ = _make()
        with patch(
            "custom_components.nikobus.binary_sensor.async_dispatcher_connect",
            return_value=lambda: None,
        ) as conn:
            _run(e.async_added_to_hass())
        signals = [c.args[1] for c in conn.call_args_list]
        self.assertIn(press_signal("081032"), signals)

    def test_second_press_cancels_prior_timer(self):
        e, _ = _make()
        cancel = MagicMock()
        e._reset_timer_cancel = cancel
        e._handle_button_event({"address": "081032"})
        cancel.assert_called_once()

    def test_reset_returns_to_idle(self):
        e, _ = _make()
        e._attr_is_on = True
        e._reset_timer_cancel = MagicMock()
        e._reset_state(None)
        self.assertFalse(e._attr_is_on)
        self.assertIsNone(e._reset_timer_cancel)

    def test_coordinator_update_is_noop(self):
        e, _ = _make()
        e._attr_is_on = True
        e._handle_coordinator_update()  # event-driven sensor ignores it
        self.assertTrue(e._attr_is_on)


if __name__ == "__main__":
    unittest.main()
