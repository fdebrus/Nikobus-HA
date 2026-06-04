"""Characterization tests for the button binary sensor.

Event-driven: a matching bus press flips it to 'pressed' and schedules a
reset back to 'idle'. Pins the address match + reset-timer behavior.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from custom_components.nikobus.binary_sensor import NikobusButtonBinarySensor


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make():
    coord = MagicMock()
    e = NikobusButtonBinarySensor(
        coord, "1A2B3C", "1A", {"bus_address": "081032"}, parent_phys=None
    )
    e.hass = MagicMock()
    return e, coord


def _press(address):
    ev = MagicMock()
    ev.data = {"address": address}
    return ev


class TestButtonBinarySensor(unittest.TestCase):
    def test_initial_state_idle(self):
        e, _ = _make()
        self.assertFalse(e._attr_is_on)
        self.assertEqual(e.state, "idle")

    def test_press_match_sets_pressed_and_schedules_reset(self):
        e, _ = _make()
        e._handle_button_event(_press("081032"))
        self.assertTrue(e._attr_is_on)
        self.assertEqual(e.state, "pressed")
        # async_call_later (stubbed) returns a cancel handle
        self.assertIsNotNone(e._reset_timer_cancel)

    def test_press_other_address_ignored(self):
        e, _ = _make()
        e._handle_button_event(_press("FFFFFF"))
        self.assertFalse(e._attr_is_on)

    def test_second_press_cancels_prior_timer(self):
        e, _ = _make()
        cancel = MagicMock()
        e._reset_timer_cancel = cancel
        e._handle_button_event(_press("081032"))
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
