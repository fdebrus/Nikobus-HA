"""A command the module never acknowledges reaches the entity.

The library queues a set-output and returns; when the exchange fails
after its last attempt it calls the ``failure_handler`` the entity passed.
The entity drops its optimistic state and logs the failure.
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.nikobus.button import _entry_background_task
from custom_components.nikobus.switch import NikobusRelaySwitchEntity


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _coord():
    c = MagicMock()
    c.api.turn_on_switch = AsyncMock()
    c.api.turn_off_switch = AsyncMock()
    c.get_switch_state = MagicMock(return_value=False)
    return c


class TestFailureReachesTheEntity(unittest.TestCase):
    def _make(self):
        c = _coord()
        e = NikobusRelaySwitchEntity(c, "3851", 3, "Relay", "Switch", "05-002")
        e.hass = MagicMock()
        e.entity_id = "switch.relay"
        e.async_write_ha_state = MagicMock()
        return e, c

    def test_the_command_carries_the_entity_handler(self):
        e, c = self._make()
        _run(e.async_turn_on())
        kwargs = c.api.turn_on_switch.await_args.kwargs
        self.assertEqual(kwargs["failure_handler"], e._command_failed)
        _run(e.async_turn_off())
        kwargs = c.api.turn_off_switch.await_args.kwargs
        self.assertEqual(kwargs["failure_handler"], e._command_failed)

    def test_a_failure_drops_the_optimistic_state_and_logs(self):
        e, _c = self._make()
        _run(e.async_turn_on())
        self.assertTrue(e._is_on)
        with self.assertLogs("custom_components.nikobus.entity", level=logging.WARNING) as logs:
            e._command_failed(TimeoutError("no acknowledgement"))
        self.assertIsNone(e._is_on)
        self.assertFalse(e.is_on)  # back to what the module last reported
        e.async_write_ha_state.assert_called()
        self.assertIn("switch.relay", logs.output[0])
        self.assertIn("no acknowledgement", logs.output[0])


class TestBackgroundTasksBelongToTheEntry(unittest.TestCase):
    def test_the_entry_creates_the_task_when_it_can(self):
        hass = MagicMock()
        coordinator = MagicMock()
        coro = MagicMock()
        _entry_background_task(hass, coordinator, coro, "nikobus_test")
        coordinator.config_entry.async_create_background_task.assert_called_once_with(
            hass, coro, name="nikobus_test"
        )
        hass.async_create_background_task.assert_not_called()

    def test_hass_is_the_fallback(self):
        hass = MagicMock()
        coordinator = MagicMock()
        coordinator.config_entry = object()  # no task API on this entry
        coro = MagicMock()
        _entry_background_task(hass, coordinator, coro, "nikobus_test")
        hass.async_create_background_task.assert_called_once_with(coro, name="nikobus_test")
