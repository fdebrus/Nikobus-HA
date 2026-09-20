"""Fan platform: a dimmer output presented as a variable-speed fan.

Same channel, same commands and state buffer as the dimmer light; only
the presentation (0-100 % speed instead of 0-255 brightness) differs.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.config_flow import _MODULE_ENTITY_TYPES
from custom_components.nikobus.fan import (
    NikobusDimmerFanEntity,
    level_to_percentage,
    percentage_to_level,
)
from custom_components.nikobus.router import build_routing


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _coord():
    c = MagicMock()
    c.api.turn_on_light = AsyncMock()
    c.api.turn_off_light = AsyncMock()
    c.get_light_brightness = MagicMock(return_value=0)
    c.get_controlled_by = MagicMock(return_value=[])
    return c


class TestConversion(unittest.TestCase):
    def test_endpoints_and_rounding(self):
        self.assertEqual(percentage_to_level(0), 0)
        self.assertEqual(percentage_to_level(100), 255)
        self.assertEqual(percentage_to_level(50), 128)
        self.assertEqual(percentage_to_level(1), 3)
        self.assertEqual(level_to_percentage(0), 0)
        self.assertEqual(level_to_percentage(255), 100)
        self.assertEqual(level_to_percentage(128), 50)
        # Any non-zero level is a running fan, never rounded down to 0 %.
        self.assertEqual(level_to_percentage(1), 1)

    def test_round_trip_is_stable(self):
        for pct in range(0, 101):
            self.assertEqual(level_to_percentage(percentage_to_level(pct)), pct)


class TestRouting(unittest.TestCase):
    def _modules(self, entity_type):
        channel = {"description": "Extractor"}
        if entity_type is not None:
            channel["entity_type"] = entity_type
        return {
            "dimmer_module": {
                "0E6C": {"description": "Dimmer", "model": "05-007-02", "channels": [channel]}
            }
        }

    def test_fan_type_routes_to_the_fan_platform(self):
        routing = build_routing(self._modules("fan"))
        self.assertEqual(routing["light"], [])
        self.assertEqual(len(routing["fan"]), 1)
        spec = routing["fan"][0]
        self.assertEqual((spec.domain, spec.kind, spec.address, spec.channel), ("fan", "dimmer_fan", "0E6C", 1))

    def test_default_and_unset_stay_a_light(self):
        for entity_type in (None, "default", "light"):
            routing = build_routing(self._modules(entity_type))
            self.assertEqual(routing["fan"], [], entity_type)
            self.assertEqual(routing["light"][0].kind, "dimmer_light", entity_type)

    def test_fan_is_not_offered_for_other_modules(self):
        routing = build_routing({
            "switch_module": {"4707": {"description": "S", "model": "05-000-02",
                                       "channels": [{"description": "x", "entity_type": "fan"}]}}
        })
        self.assertEqual(routing["fan"], [])
        self.assertEqual(routing["switch"][0].kind, "relay_switch")

    def test_customize_dropdown_offers_fan_for_dimmers_only(self):
        self.assertIn("fan", _MODULE_ENTITY_TYPES["dimmer_module"])
        self.assertNotIn("fan", _MODULE_ENTITY_TYPES["switch_module"])
        self.assertNotIn("fan", _MODULE_ENTITY_TYPES["roller_module"])


class TestDimmerFan(unittest.TestCase):
    def _make(self):
        c = _coord()
        return NikobusDimmerFanEntity(c, "0E6C", 5, "Extractor", "Dimmer", "05-007-02"), c

    def test_unique_id_and_attributes(self):
        e, _ = self._make()
        self.assertEqual(e._attr_unique_id, "nikobus_fan_dimmer_fan_0E6C_5")
        attrs = e.extra_state_attributes
        self.assertEqual((attrs["nikobus_address"], attrs["channel"]), ("0E6C", 5))

    def test_is_on_and_percentage_follow_the_buffer(self):
        e, c = self._make()
        c.get_light_brightness.return_value = 128
        self.assertTrue(e.is_on)
        self.assertEqual(e.percentage, 50)
        c.get_light_brightness.return_value = 0
        self.assertFalse(e.is_on)
        self.assertEqual(e.percentage, 0)

    def test_turn_on_default_is_full_speed(self):
        e, c = self._make()
        c.get_light_brightness.return_value = 40
        _run(e.async_turn_on())
        self.assertTrue(e._is_on)
        self.assertEqual(e.percentage, 100)
        c.api.turn_on_light.assert_awaited_once_with("0E6C", 5, 255, current_brightness=40)

    def test_set_percentage_maps_to_a_level(self):
        e, c = self._make()
        _run(e.async_set_percentage(50))
        self.assertEqual(c.api.turn_on_light.await_args.args[2], 128)
        self.assertEqual(e.percentage, 50)

    def test_zero_percent_turns_off(self):
        e, c = self._make()
        c.get_light_brightness.return_value = 128
        _run(e.async_set_percentage(0))
        c.api.turn_on_light.assert_not_awaited()
        c.api.turn_off_light.assert_awaited_once_with("0E6C", 5, current_brightness=128)
        self.assertFalse(e.is_on)

    def test_turn_on_reverts_on_error(self):
        e, c = self._make()
        c.api.turn_on_light.side_effect = RuntimeError("bus down")
        with self.assertRaises(HomeAssistantError) as cm:
            _run(e.async_turn_on(percentage=30))
        self.assertEqual(cm.exception.translation_key, "communication_error")
        self.assertIsNone(e._is_on)
        self.assertIsNone(e._optimistic_level)

    def test_button_operation_clears_optimistic_state(self):
        e, _ = self._make()
        e._optimistic_level = 100
        e._is_on = True
        e.async_write_ha_state = MagicMock()
        e._handle_button_operation()
        self.assertIsNone(e._optimistic_level)
        self.assertIsNone(e._is_on)
        e.async_write_ha_state.assert_called_once()
