"""Characterization tests for the relay / cover switch entities.

(The input A/B latch switch is covered by test_input_latch_switch.py.)
Covers optimistic state, the bus command each issues, and revert-on-error.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.switch import (
    NikobusRelaySwitchEntity,
    NikobusCoverSwitchEntity,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _coord():
    c = MagicMock()
    c.api.turn_on_switch = AsyncMock()
    c.api.turn_off_switch = AsyncMock()
    c.api.open_cover = AsyncMock()
    c.api.stop_cover = AsyncMock()
    c.get_switch_state = MagicMock(return_value=False)
    c.get_cover_state = MagicMock(return_value=0x00)
    return c


class TestRelaySwitch(unittest.TestCase):
    def _make(self):
        c = _coord()
        return NikobusRelaySwitchEntity(c, "3851", 3, "Relay", "Switch", "05-002"), c

    def test_is_on_optimistic_then_coordinator(self):
        e, c = self._make()
        e._is_on = True
        self.assertTrue(e.is_on)
        e._is_on = None
        c.get_switch_state.return_value = True
        self.assertTrue(e.is_on)
        c.get_switch_state.return_value = False
        self.assertFalse(e.is_on)

    def test_turn_on_off(self):
        e, c = self._make()
        _run(e.async_turn_on())
        self.assertTrue(e._is_on)
        c.api.turn_on_switch.assert_awaited_once_with("3851", 3)
        _run(e.async_turn_off())
        self.assertFalse(e._is_on)
        c.api.turn_off_switch.assert_awaited_once_with("3851", 3)

    def test_turn_on_reverts_on_error(self):
        e, c = self._make()
        c.api.turn_on_switch.side_effect = RuntimeError("x")
        with self.assertRaises(HomeAssistantError) as cm:
            _run(e.async_turn_on())
        self.assertEqual(cm.exception.translation_key, "communication_error")
        self.assertIsInstance(cm.exception.__cause__, RuntimeError)
        self.assertIsNone(e._is_on)


class TestCoverSwitch(unittest.TestCase):
    def _make(self):
        c = _coord()
        return NikobusCoverSwitchEntity(c, "9105", 1, "CoverSwitch", "Roller", "05-001"), c

    def test_is_on_from_cover_state(self):
        e, c = self._make()
        c.get_cover_state.return_value = 0x01
        self.assertTrue(e.is_on)
        c.get_cover_state.return_value = 0x02
        self.assertFalse(e.is_on)

    def test_turn_on_opens(self):
        e, c = self._make()
        _run(e.async_turn_on())
        c.api.open_cover.assert_awaited_once_with("9105", 1)

    def test_turn_off_stops_closing(self):
        e, c = self._make()
        _run(e.async_turn_off())
        c.api.stop_cover.assert_awaited_once_with("9105", 1, direction="closing")

    def test_turn_off_reverts_on_error(self):
        e, c = self._make()
        c.api.stop_cover.side_effect = RuntimeError("x")
        with self.assertRaises(HomeAssistantError) as cm:
            _run(e.async_turn_off())
        self.assertEqual(cm.exception.translation_key, "communication_error")
        self.assertIsNone(e._is_on)


if __name__ == "__main__":
    unittest.main()
