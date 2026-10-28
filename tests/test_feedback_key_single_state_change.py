"""A light driven by its feedback key changes state once per switch.

Reported by Peter on 3.30.3: the library reads the module before pressing
the key and again after it; the answer to the first read (ch 8 still off,
0.43 s after the tap) woke the entity, which dropped its optimistic ON
and showed OFF until the second read (1.27 s). Three recorder entries
per switch, and automations on the entity's state fired on the
intermediate one. The entity now holds the state it was asked for until
the command is complete.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.light import NikobusRelayEntity
from custom_components.nikobus.switch import NikobusRelaySwitchEntity


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _entity(cls, has_key: bool, module_state: bool = False):
    c = MagicMock()
    c.has_feedback_key = MagicMock(return_value=has_key)
    c.async_module_state_read = MagicMock()
    c.get_switch_state = MagicMock(return_value=module_state)
    e = cls(c, "C7C1", 8, "Bureau LED balk", "Switch", "05-000-02")
    e.hass = MagicMock()
    e.entity_id = "light.verlichting_bureau_led_balk"
    e.async_write_ha_state = MagicMock()
    return e, c


class _Cases:
    cls = NikobusRelayEntity

    def _switch_with_wakes(self, e, c, method, *, fail=False):
        """The library call, during which both reads wake the module's entities."""
        seen: list[bool] = []

        async def _api(address, channel, failure_handler=None):
            # Answer to the read before the press: module says still off.
            e._handle_coordinator_update()
            e._handle_button_operation()
            seen.append(e.is_on)
            if fail:
                raise RuntimeError("bus")

        setattr(c.api, method, AsyncMock(side_effect=_api))
        return seen

    def test_turn_on_shows_on_throughout_and_reads_once_at_the_end(self):
        e, c = _entity(self.cls, True, module_state=False)
        seen = self._switch_with_wakes(e, c, "turn_on_switch")
        _run(e.async_turn_on())
        self.assertEqual(seen, [True])  # the pre-read wake did not flip it
        self.assertFalse(e._switching)
        c.async_module_state_read.assert_called_once_with("C7C1", 8)
        # One state write: the optimistic one. The read state comes via the
        # module wake that async_module_state_read sends.
        self.assertEqual(e.async_write_ha_state.call_count, 1)

    def test_turn_off_shows_off_throughout(self):
        e, c = _entity(self.cls, True, module_state=True)
        seen = self._switch_with_wakes(e, c, "turn_off_switch")
        _run(e.async_turn_off())
        self.assertEqual(seen, [False])
        self.assertFalse(e._switching)

    def test_after_the_command_wakes_are_handled_again(self):
        e, c = _entity(self.cls, True, module_state=False)
        self._switch_with_wakes(e, c, "turn_on_switch")
        _run(e.async_turn_on())
        e._handle_button_operation()  # the read after the press
        self.assertIsNone(e._is_on)
        self.assertFalse(e.is_on)  # shows what the module reports

    def test_a_failed_command_releases_the_hold(self):
        e, c = _entity(self.cls, True)
        self._switch_with_wakes(e, c, "turn_on_switch", fail=True)
        with self.assertRaises(HomeAssistantError):
            _run(e.async_turn_on())
        self.assertFalse(e._switching)
        self.assertIsNone(e._is_on)

    def test_without_a_key_wakes_are_never_held(self):
        e, c = _entity(self.cls, False)
        seen = self._switch_with_wakes(e, c, "turn_on_switch")
        _run(e.async_turn_on())
        self.assertEqual(seen, [False])  # a set-output path: the wake clears the optimistic state
        self.assertFalse(e._switching)
        c.async_module_state_read.assert_not_called()

    def test_toggle_holds_too(self):
        e, c = _entity(self.cls, True, module_state=True)

        async def _toggle(address, channel, failure_handler=None):
            e._handle_coordinator_update()
            self.assertTrue(e._switching)
            return False

        c.api.toggle_switch = AsyncMock(side_effect=_toggle)
        _run(e.async_toggle())
        self.assertFalse(e._switching)
        c.async_module_state_read.assert_called_once_with("C7C1", 8)


class TestRelayLight(_Cases, unittest.TestCase):
    cls = NikobusRelayEntity


class TestRelaySwitch(_Cases, unittest.TestCase):
    cls = NikobusRelaySwitchEntity
