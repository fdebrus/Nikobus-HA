"""Tests for repeated simulated-button-press emission.

A single ``#N`` frame is unreliable on the Nikobus bus (modules only act
on a command seen at least twice), so HA-originated presses are emitted
as a short, spaced burst via ``coordinator.async_send_button_press``.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.nikobus.coordinator import NikobusDataCoordinator
from custom_components.nikobus.const import DEFAULT_PRESS_REPEAT


def _coord(press_repeat=DEFAULT_PRESS_REPEAT):
    c = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    c._press_repeat = press_repeat
    c.nikobus_command = AsyncMock()
    return c


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestButtonPressRepeat(unittest.TestCase):
    def test_default_sends_three_times(self):
        c = _coord()
        with patch("custom_components.nikobus.coordinator.asyncio.sleep",
                   new=AsyncMock()):
            _run(c.async_send_button_press("9E4E2C"))
        sent = [args.args[0] for args in c.nikobus_command.queue_command.call_args_list]
        self.assertEqual(sent, ["#N9E4E2C\r#E1"] * 3)

    def test_configurable_repeat_count(self):
        c = _coord(press_repeat=2)
        with patch("custom_components.nikobus.coordinator.asyncio.sleep",
                   new=AsyncMock()):
            _run(c.async_send_button_press("DE4E2C"))
        self.assertEqual(c.nikobus_command.queue_command.await_count, 2)

    def test_spacing_between_repeats(self):
        """N frames => N-1 inter-frame gaps (no trailing sleep)."""
        c = _coord(press_repeat=3)
        sleep = AsyncMock()
        with patch("custom_components.nikobus.coordinator.asyncio.sleep", new=sleep):
            _run(c.async_send_button_press("ABCDEF"))
        self.assertEqual(sleep.await_count, 2)

    def test_count_floor_is_one(self):
        c = _coord(press_repeat=0)  # misconfig / disabled -> still send once
        with patch("custom_components.nikobus.coordinator.asyncio.sleep",
                   new=AsyncMock()):
            _run(c.async_send_button_press("112233"))
        self.assertEqual(c.nikobus_command.queue_command.await_count, 1)

    def test_empty_address_sends_nothing(self):
        c = _coord()
        _run(c.async_send_button_press(""))
        c.nikobus_command.queue_command.assert_not_called()

    def test_event_handler_routes_through_burst(self):
        c = _coord()
        c.hass = None
        with patch.object(c, "async_send_button_press", new=AsyncMock()) as burst:
            _run(c.async_event_handler("ha_button_pressed", {"address": "9E4E2C"}))
        burst.assert_awaited_once_with("9E4E2C")


if __name__ == "__main__":
    unittest.main()
