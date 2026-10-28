"""Relay light and relay switch on a channel driven by its feedback key.

The library reads the channel, presses the key only when needed and reads
it again (nikobus-connect 0.54.0); the entity then shows what the module
reported. A toggle is decided from the module's state, not from what Home
Assistant shows. Without a key nothing changes.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus import light as light_platform
from custom_components.nikobus import switch as switch_platform
from custom_components.nikobus.light import NikobusRelayEntity
from custom_components.nikobus.switch import NikobusRelaySwitchEntity


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _coord(has_key: bool):
    c = MagicMock()
    c.api.turn_on_switch = AsyncMock()
    c.api.turn_off_switch = AsyncMock()
    c.api.toggle_switch = AsyncMock(return_value=False)
    c.has_feedback_key = MagicMock(return_value=has_key)
    c.async_module_state_read = MagicMock()
    c.get_switch_state = MagicMock(return_value=True)
    return c


def _light(has_key: bool):
    c = _coord(has_key)
    return NikobusRelayEntity(c, "C9D7", 11, "Terras LED strip", "Switch", "05-000-02"), c


def _switch(has_key: bool):
    c = _coord(has_key)
    return NikobusRelaySwitchEntity(c, "C9D7", 11, "Terras LED strip", "Switch", "05-000-02"), c


class _Cases:
    make = staticmethod(_light)
    base = light_platform.LightEntity

    def test_turn_off_with_a_key_shows_the_read_state(self):
        e, c = self.make(True)
        _run(e.async_turn_off())
        c.api.turn_off_switch.assert_awaited_once_with("C9D7", 11, failure_handler=ANY)
        c.async_module_state_read.assert_called_once_with("C9D7", 11)

    def test_turn_on_without_a_key_keeps_the_optimistic_state(self):
        e, c = self.make(False)
        _run(e.async_turn_on())
        c.async_module_state_read.assert_not_called()
        self.assertTrue(e._is_on)

    def test_toggle_with_a_key_asks_the_module(self):
        e, c = self.make(True)
        e._is_on = False  # Home Assistant believes off; the module decides
        _run(e.async_toggle())
        c.api.toggle_switch.assert_awaited_once_with("C9D7", 11, failure_handler=ANY)
        c.api.turn_on_switch.assert_not_called()
        c.async_module_state_read.assert_called_once_with("C9D7", 11)

    def test_toggle_without_a_key_is_home_assistants(self):
        e, c = self.make(False)
        with patch.object(self.base, "async_toggle", AsyncMock(), create=True) as default:
            _run(e.async_toggle())
        default.assert_awaited_once()
        c.api.toggle_switch.assert_not_called()

    def test_toggle_failure_is_a_communication_error(self):
        e, c = self.make(True)
        c.api.toggle_switch.side_effect = RuntimeError("x")
        with self.assertRaises(HomeAssistantError) as cm:
            _run(e.async_toggle())
        self.assertEqual(cm.exception.translation_key, "communication_error")
        c.async_module_state_read.assert_not_called()


class TestRelayLight(_Cases, unittest.TestCase):
    make = staticmethod(_light)
    base = light_platform.LightEntity


class TestRelaySwitch(_Cases, unittest.TestCase):
    make = staticmethod(_switch)
    base = switch_platform.SwitchEntity


class TestCoordinatorHelpers(unittest.TestCase):
    def test_has_feedback_key_asks_the_api(self):
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        c = MagicMock()
        c.api.has_feedback_key = MagicMock(return_value=True)
        self.assertTrue(NikobusDataCoordinator.has_feedback_key(c, "C9D7", 11))
        c.api.has_feedback_key.assert_called_once_with("switch_module", "C9D7", 11)
        c.api = None
        self.assertFalse(NikobusDataCoordinator.has_feedback_key(c, "C9D7", 11))

    def test_state_read_marks_the_group_and_wakes_the_module(self):
        from custom_components.nikobus.const import operation_signal
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        c = MagicMock()
        c.nikobus_command.group_state_age = MagicMock(return_value=0.4)  # the module just answered
        with patch("custom_components.nikobus.coordinator.async_dispatcher_send") as send:
            NikobusDataCoordinator.async_module_state_read(c, "c9d7", 11)
        c._note_state_seen.assert_called_once_with("c9d7", 2)
        send.assert_called_once_with(c.hass, operation_signal("C9D7"))

    def test_a_module_that_did_not_answer_is_not_marked_heard(self):
        """The library falls back to a direct write when its read goes
        unanswered; the entity still wakes, but a silent module must not
        clear its back-off or Repair issue."""
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        c = MagicMock()
        c.nikobus_command.group_state_age = MagicMock(return_value=None)
        with patch("custom_components.nikobus.coordinator.async_dispatcher_send") as send:
            NikobusDataCoordinator.async_module_state_read(c, "C9D7", 11)
        c._note_state_seen.assert_not_called()
        send.assert_called_once()
        c.nikobus_command.group_state_age = MagicMock(return_value=120.0)  # an old answer
        NikobusDataCoordinator.async_module_state_read(c, "C9D7", 11)
        c._note_state_seen.assert_not_called()
