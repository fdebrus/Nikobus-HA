"""Characterization tests for the scene platform.

Covers the software-scene helpers (state→byte mapping, feedback-LED
normalization + dispatch) and CF-scene activation. The full async_activate
fan-out (module writes + timed roller stops) is integration-level and left
to the live system; these pin the deterministic pieces.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.nikobus.scene import (
    NikobusSceneEntity,
    NikobusCFSceneEntity,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _scene(**cfg):
    base = {"id": "s1", "channels": [], "feedback_led": None}
    base.update(cfg)
    return NikobusSceneEntity(MagicMock(), base)


class TestStateToByte(unittest.TestCase):
    def test_dimmer_clamps_0_255(self):
        e = _scene()
        self.assertEqual(e._state_to_byte("dimmer_module", "128"), 128)
        self.assertEqual(e._state_to_byte("dimmer_module", 300), 255)
        self.assertEqual(e._state_to_byte("dimmer_module", -5), 0)

    def test_dimmer_invalid_is_none(self):
        e = _scene()
        self.assertIsNone(e._state_to_byte("dimmer_module", "abc"))

    def test_switch_on_off(self):
        e = _scene()
        self.assertEqual(e._state_to_byte("switch_module", "on"), 0xFF)
        self.assertEqual(e._state_to_byte("switch_module", "off"), 0x00)
        self.assertEqual(e._state_to_byte("switch_module", "true"), 0xFF)

    def test_roller_open_close_stop(self):
        e = _scene()
        self.assertEqual(e._state_to_byte("roller_module", "open"), 0x01)
        self.assertEqual(e._state_to_byte("roller_module", "close"), 0x02)
        self.assertEqual(e._state_to_byte("roller_module", "stop"), 0x00)

    def test_unknown_type_or_value_is_none(self):
        e = _scene()
        self.assertIsNone(e._state_to_byte("nope_module", "on"))
        self.assertIsNone(e._state_to_byte("switch_module", "weird"))


class TestNormalizeFeedbackLeds(unittest.TestCase):
    def test_none(self):
        self.assertEqual(_scene()._normalize_feedback_leds(None), [])

    def test_single_string_stripped(self):
        self.assertEqual(_scene()._normalize_feedback_leds("  AABBCC "), ["AABBCC"])

    def test_list_strips_and_drops_empty(self):
        self.assertEqual(
            _scene()._normalize_feedback_leds(["A", " B ", ""]), ["A", "B"]
        )


class TestFeedbackLedDispatch(unittest.TestCase):
    def test_each_led_sent_as_press(self):
        e = _scene(feedback_led=["AABBCC", "DDEEFF"])
        e.coordinator.async_send_button_press = AsyncMock()
        _run(e._process_feedback_leds())
        self.assertEqual(
            [c.args[0] for c in e.coordinator.async_send_button_press.await_args_list],
            ["AABBCC", "DDEEFF"],
        )


class TestCFSceneActivation(unittest.TestCase):
    def test_activate_broadcasts_bus_address(self):
        coord = MagicMock()
        coord.async_activate_cf_broadcast = AsyncMock()
        e = NikobusCFSceneEntity(
            coord,
            bus_address="de4e2c",
            cf_config={"pattern": "light_scene", "outputs": []},
        )
        _run(e.async_activate())
        coord.async_activate_cf_broadcast.assert_awaited_once_with("DE4E2C")


if __name__ == "__main__":
    unittest.main()
