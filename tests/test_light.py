"""Characterization tests for the Nikobus light platform.

Covers the dimmer / relay / cover-as-light entities: optimistic state,
brightness composition, the bus command each issues, and the
revert-on-error path. No source changes.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.nikobus.const import operation_signal
from custom_components.nikobus.light import (
    NikobusDimmerEntity,
    NikobusRelayEntity,
    NikobusCoverLightEntity,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _coord():
    c = MagicMock()
    c.api.turn_on_light = AsyncMock()
    c.api.turn_off_light = AsyncMock()
    c.api.turn_on_switch = AsyncMock()
    c.api.turn_off_switch = AsyncMock()
    c.api.open_cover = AsyncMock()
    c.api.stop_cover = AsyncMock()
    c.get_light_brightness = MagicMock(return_value=0)
    c.get_switch_state = MagicMock(return_value=False)
    c.get_cover_state = MagicMock(return_value=0x00)
    return c


class TestDimmer(unittest.TestCase):
    def _make(self):
        c = _coord()
        return NikobusDimmerEntity(c, "0E6C", 1, "Lamp", "Dimmer", "05-007"), c

    def test_is_on_optimistic_then_brightness(self):
        e, c = self._make()
        e._is_on = True
        self.assertTrue(e.is_on)
        e._is_on = None
        c.get_light_brightness.return_value = 120
        self.assertTrue(e.is_on)
        c.get_light_brightness.return_value = 0
        self.assertFalse(e.is_on)

    def test_brightness_optimistic_then_coordinator(self):
        e, c = self._make()
        e._optimistic_brightness = 200
        self.assertEqual(e.brightness, 200)
        e._optimistic_brightness = None
        c.get_light_brightness.return_value = 77
        self.assertEqual(e.brightness, 77)

    def test_turn_on_default_full_brightness(self):
        e, c = self._make()
        c.get_light_brightness.return_value = 40
        _run(e.async_turn_on())
        self.assertTrue(e._is_on)
        self.assertEqual(e._optimistic_brightness, 255)
        c.api.turn_on_light.assert_awaited_once_with(
            "0E6C", 1, 255, current_brightness=40
        )

    def test_turn_on_with_brightness(self):
        e, c = self._make()
        _run(e.async_turn_on(brightness=128))
        self.assertEqual(e._optimistic_brightness, 128)
        self.assertEqual(c.api.turn_on_light.await_args.args[2], 128)

    def test_turn_on_reverts_on_error(self):
        e, c = self._make()
        c.api.turn_on_light.side_effect = RuntimeError("bus down")
        with self.assertRaises(RuntimeError):
            _run(e.async_turn_on())
        self.assertIsNone(e._is_on)
        self.assertIsNone(e._optimistic_brightness)

    def test_turn_off(self):
        e, c = self._make()
        c.get_light_brightness.return_value = 90
        _run(e.async_turn_off())
        self.assertFalse(e._is_on)
        self.assertIsNone(e._optimistic_brightness)
        c.api.turn_off_light.assert_awaited_once_with("0E6C", 1, current_brightness=90)

    def test_button_operation_clears_optimistic_state(self):
        # The signal is per-module, so any delivery is for this dimmer.
        e, _ = self._make()
        e._optimistic_brightness = 100
        e._is_on = True
        e.async_write_ha_state = MagicMock()
        e._handle_button_operation()
        self.assertIsNone(e._optimistic_brightness)
        self.assertIsNone(e._is_on)

    def test_subscribes_to_its_module_operation_signal(self):
        e, _ = self._make()
        e.hass = MagicMock()
        with patch(
            "custom_components.nikobus.light.async_dispatcher_connect",
            return_value=lambda: None,
        ) as conn:
            _run(e.async_added_to_hass())
        signals = [c.args[1] for c in conn.call_args_list]
        self.assertIn(operation_signal("0E6C"), signals)


class TestRelayLight(unittest.TestCase):
    def _make(self):
        c = _coord()
        return NikobusRelayEntity(c, "3851", 2, "Relay", "Switch", "05-002"), c

    def test_is_on_optimistic_then_coordinator(self):
        e, c = self._make()
        e._is_on = True
        self.assertTrue(e.is_on)
        e._is_on = None
        c.get_switch_state.return_value = True
        self.assertTrue(e.is_on)

    def test_turn_on_off(self):
        e, c = self._make()
        _run(e.async_turn_on())
        self.assertTrue(e._is_on)
        c.api.turn_on_switch.assert_awaited_once_with("3851", 2)
        _run(e.async_turn_off())
        self.assertFalse(e._is_on)
        c.api.turn_off_switch.assert_awaited_once_with("3851", 2)

    def test_turn_on_reverts_on_error(self):
        e, c = self._make()
        c.api.turn_on_switch.side_effect = RuntimeError("x")
        with self.assertRaises(RuntimeError):
            _run(e.async_turn_on())
        self.assertIsNone(e._is_on)


class TestCoverLight(unittest.TestCase):
    def _make(self):
        c = _coord()
        return NikobusCoverLightEntity(c, "9105", 1, "CoverLight", "Roller", "05-001"), c

    def test_is_on_from_cover_state(self):
        e, c = self._make()
        c.get_cover_state.return_value = 0x01
        self.assertTrue(e.is_on)
        c.get_cover_state.return_value = 0x00
        self.assertFalse(e.is_on)

    def test_turn_on_opens_cover(self):
        e, c = self._make()
        _run(e.async_turn_on())
        c.api.open_cover.assert_awaited_once_with("9105", 1)

    def test_turn_off_stops_cover_closing(self):
        e, c = self._make()
        _run(e.async_turn_off())
        c.api.stop_cover.assert_awaited_once_with("9105", 1, direction="closing")


if __name__ == "__main__":
    unittest.main()
