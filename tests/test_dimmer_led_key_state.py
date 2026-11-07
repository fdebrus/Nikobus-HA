"""A dimmer driven by its LED key keeps the state it was asked for.

Reported on 3.31.3 (05-008-02, LED key in mode M14): with an LED key the
library reads the channel before deciding whether to press the key
(nikobus-connect 0.57.3). The answer, still the old state, woke the
module's entities 0.43 s after the call and replaced the optimistic
state; the set-output that followed put the new value in the buffer but
woke nobody, so the entity showed the old state until the next poll or
Feedback Module read, 30 s to 3.5 min later.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.light import NikobusDimmerEntity


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _dimmer(has_key: bool, buffer_brightness: int):
    c = MagicMock()
    c.has_feedback_key = MagicMock(return_value=has_key)
    c.get_light_brightness = MagicMock(return_value=buffer_brightness)
    e = NikobusDimmerEntity(c, "C95D", 2, "Uplighter", "Dimmer", "05-008-02")
    e.hass = MagicMock()
    e.entity_id = "light.uplighter"
    e.async_write_ha_state = MagicMock()
    return e, c


def _wake_during_call(e, c, method, *, fail=False):
    """The library call, during which the answer to its read wakes the module."""
    seen: list[tuple[bool, int]] = []

    async def _api(*args, **kwargs):
        e._handle_coordinator_update()
        e._handle_button_operation()
        seen.append((e.is_on, e.brightness))
        if fail:
            raise RuntimeError("bus")

    setattr(c.api, method, AsyncMock(side_effect=_api))
    return seen


class TestDimmerLedKeyKeepsTheAskedState(unittest.TestCase):
    def test_turn_on_is_not_reverted_by_the_read_before_the_press(self):
        e, c = _dimmer(True, buffer_brightness=0)
        seen = _wake_during_call(e, c, "turn_on_light")
        _run(e.async_turn_on())
        self.assertEqual(seen, [(True, 255)])
        self.assertEqual((e.is_on, e.brightness), (True, 255))
        c.has_feedback_key.assert_called_once_with("C95D", 2, "dimmer_module")

    def test_turn_off_is_not_reverted_by_the_read_before_the_press(self):
        e, c = _dimmer(True, buffer_brightness=255)
        seen = _wake_during_call(e, c, "turn_off_light")
        _run(e.async_turn_off())
        self.assertEqual([on for on, _ in seen], [False])
        self.assertFalse(e.is_on)

    def test_after_the_call_the_module_state_is_shown_again(self):
        e, c = _dimmer(True, buffer_brightness=0)
        _wake_during_call(e, c, "turn_on_light")
        _run(e.async_turn_on())
        # The next poll: the write is in the buffer by now.
        c.get_light_brightness.return_value = 255
        e._handle_coordinator_update()
        self.assertIsNone(e._optimistic_brightness)
        self.assertEqual((e.is_on, e.brightness), (True, 255))

    def test_a_failed_call_releases_the_entity(self):
        e, c = _dimmer(True, buffer_brightness=0)
        _wake_during_call(e, c, "turn_on_light", fail=True)
        with self.assertRaises(HomeAssistantError):
            _run(e.async_turn_on())
        self.assertFalse(e._commanding)
        self.assertFalse(e.is_on)

    def test_without_an_led_key_updates_are_handled_during_the_call(self):
        # No read happens before a plain set-output, so nothing is held back.
        e, c = _dimmer(False, buffer_brightness=0)
        seen = _wake_during_call(e, c, "turn_on_light")
        _run(e.async_turn_on())
        self.assertEqual(seen, [(False, 0)])
