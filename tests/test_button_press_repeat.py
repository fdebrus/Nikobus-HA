"""Tests for repeated simulated-button-press emission.

A single ``#N`` frame is unreliable on the Nikobus bus (modules only act
on a command seen at least twice), so HA-originated presses are emitted
as a short, spaced burst via ``coordinator.async_send_button_press``.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.nikobus.const import DEFAULT_PRESS_REPEAT
from custom_components.nikobus.coordinator import NikobusDataCoordinator
from nikobus_connect.api import NikobusAPI


def _coord(press_repeat=DEFAULT_PRESS_REPEAT, actuator=True):
    c = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    c._press_repeat = press_repeat
    c.nikobus_command = AsyncMock()
    # The real library API, so the burst text is the one that goes out.
    c.api = NikobusAPI(c.nikobus_command, {})
    c.nikobus_actuator = AsyncMock() if actuator else None
    return c


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestButtonPressRepeat(unittest.TestCase):
    """A press is ONE queued write carrying the repeats back to back."""

    def test_default_is_one_write_with_three_frames(self):
        c = _coord()
        _run(c.async_send_button_press("9E4E2C"))
        c.nikobus_command.queue_command.assert_awaited_once()
        sent = c.nikobus_command.queue_command.await_args.args[0]
        self.assertEqual(sent, "\r".join(["#N9E4E2C\r#E1"] * 3))

    def test_configurable_repeat_count(self):
        c = _coord(press_repeat=2)
        _run(c.async_send_button_press("DE4E2C"))
        sent = c.nikobus_command.queue_command.await_args.args[0]
        self.assertEqual(sent.count("#N"), 2)

    def test_count_floor_is_one(self):
        c = _coord(press_repeat=0)  # misconfig / disabled -> still send once
        _run(c.async_send_button_press("112233"))
        sent = c.nikobus_command.queue_command.await_args.args[0]
        self.assertEqual(sent.count("#N"), 1)

    def test_empty_address_sends_nothing(self):
        c = _coord()
        _run(c.async_send_button_press(""))
        c.nikobus_command.queue_command.assert_not_called()

    def test_refresh_runs_once_the_burst_is_on_the_wire(self):
        """The completion handler (called by the queue after the write)
        refreshes the impacted modules, like an inbound press would."""
        c = _coord()
        _run(c.async_send_button_press("9e4e2c"))
        handler = c.nikobus_command.queue_command.await_args.kwargs["completion_handler"]
        c.nikobus_actuator.refresh_after_host_press.assert_not_awaited()
        _run(handler())
        c.nikobus_actuator.refresh_after_host_press.assert_awaited_once_with("9E4E2C")

    def test_no_actuator_no_refresh_but_press_still_sent(self):
        c = _coord(actuator=False)
        _run(c.async_send_button_press("9E4E2C"))
        handler = c.nikobus_command.queue_command.await_args.kwargs["completion_handler"]
        _run(handler())  # must not raise
        c.nikobus_command.queue_command.assert_awaited_once()

    def test_event_handler_routes_through_burst(self):
        c = _coord()
        c.hass = None
        with patch.object(c, "async_send_button_press", new=AsyncMock()) as burst:
            _run(c.async_event_handler("ha_button_pressed", {"address": "9E4E2C"}))
        burst.assert_awaited_once_with("9E4E2C")


if __name__ == "__main__":
    unittest.main()
