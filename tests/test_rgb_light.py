"""The RGB controller's light: a readout of the polled image, switched by
pressing the keys the .nkb says drive it (Nikobus-HA #519)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.coordinator import NikobusDataCoordinator
from custom_components.nikobus.light import NikobusRgbLight
from custom_components.nikobus.nkbreconcile import apply_rgb_links
from custom_components.nikobus.router import (
    choose_rgb_key,
    rgb_light_unique_id,
    rgb_links_for,
)

LINKS_M19 = [
    {"bus_address": "1B1492", "button_address": "124A36", "key": "1C", "mode": 19,
     "mode_label": "M19 (Start/stop scenario)", "role": "start_stop"},
    {"bus_address": "5B1492", "button_address": "124A36", "key": "1D", "mode": 19,
     "mode_label": "M19 (Start/stop scenario)", "role": "off"},
]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _coord(image: str = "00" * 6, links=None):
    c = MagicMock()
    c.get_bytearray_group_state = MagicMock(return_value=bytearray.fromhex(image))
    c.async_send_button_press = AsyncMock()
    c.get_controlled_by = MagicMock(return_value=[])
    c.dict_module_data = {"rgb_module": {"801D": {
        "module_type": "rgb_module", "model": "340-00112", "rgb_links": links or [],
    }}}
    return c


def _light(image="00" * 6, links=None):
    c = _coord(image, links)
    light = NikobusRgbLight(c, "801D", "RGB Controller (kleur mode)", "340-00112")
    light.hass = MagicMock()
    light.async_write_ha_state = MagicMock()
    return light, c


class TestReadout(unittest.TestCase):
    def test_identity(self):
        light, _ = _light()
        self.assertEqual(light._attr_unique_id, "nikobus_light_rgb_controller_801D_1")
        self.assertEqual(light._attr_unique_id, rgb_light_unique_id("801d"))
        self.assertIsNone(light._attr_name)  # the device's name is the light's

    def test_off_and_on_follow_the_polled_flag(self):
        light, c = _light("000000000000")
        self.assertFalse(light.is_on)
        c.get_bytearray_group_state.return_value = bytearray.fromhex("FFFF00FF0000")
        self.assertTrue(light.is_on)

    def test_the_colour_is_the_mix_of_lit_outputs(self):
        light, _ = _light("FFFF00FF0000", LINKS_M19)
        attrs = light.extra_state_attributes
        self.assertEqual(attrs["colour"], "magenta")
        self.assertEqual((attrs["red"], attrs["green"], attrs["blue"]), (True, False, True))
        self.assertEqual(attrs["on_key"], "1B1492")
        self.assertEqual(attrs["off_key"], "5B1492")
        self.assertEqual(attrs["linked_keys"][0]["wall_button_key"], "1C")
        self.assertEqual(attrs["linked_keys"][0]["role"], "start_stop")

    def test_render_state_diffs_on_colour_too(self):
        light, c = _light("FFFF00FF0000")
        first = light._render_state()
        c.get_bytearray_group_state.return_value = bytearray.fromhex("FF00FFFF0000")
        self.assertNotEqual(first, light._render_state())


class TestSwitching(unittest.TestCase):
    def test_turn_on_presses_the_start_stop_key_when_off(self):
        light, c = _light("00" * 6, LINKS_M19)
        _run(light.async_turn_on())
        c.async_send_button_press.assert_awaited_once_with("1B1492")
        self.assertTrue(light.is_on)

    def test_turn_off_presses_the_off_key_when_on(self):
        light, c = _light("FFFFFFFF0000", LINKS_M19)
        _run(light.async_turn_off())
        c.async_send_button_press.assert_awaited_once_with("5B1492")
        self.assertFalse(light.is_on)

    def test_no_press_when_already_in_the_asked_state(self):
        """A start/stop key would stop a running loop; a toggle would
        switch off. Nothing is pressed when the light already is what
        the caller asked for."""
        light, c = _light("FFFFFFFF0000", LINKS_M19)
        _run(light.async_turn_on())
        c.async_send_button_press.assert_not_awaited()
        light, c = _light("00" * 6, LINKS_M19)
        _run(light.async_turn_off())
        c.async_send_button_press.assert_not_awaited()

    def test_a_toggle_key_serves_both_ways(self):
        toggle = [{"bus_address": "1B1492", "button_address": "124A36", "key": "1A", "mode": 13,
                   "mode_label": "M13 (Dim on/off (1 button))", "role": "toggle"}]
        light, c = _light("00" * 6, toggle)
        _run(light.async_turn_on())
        c.async_send_button_press.assert_awaited_once_with("1B1492")
        light, c = _light("FF000000" + "0000", toggle)
        _run(light.async_turn_off())
        c.async_send_button_press.assert_awaited_once_with("1B1492")

    def test_without_a_known_key_switching_is_refused_with_a_clear_error(self):
        light, c = _light("00" * 6, [])
        with self.assertRaises(HomeAssistantError) as ctx:
            _run(light.async_turn_on())
        self.assertEqual(ctx.exception.translation_key, "rgb_no_key")
        c.async_send_button_press.assert_not_awaited()

    def test_a_failed_press_reverts_and_surfaces_as_an_ha_error(self):
        light, c = _light("00" * 6, LINKS_M19)
        c.async_send_button_press.side_effect = RuntimeError("bus down")
        with self.assertRaises(HomeAssistantError):
            _run(light.async_turn_on())
        self.assertFalse(light.is_on)


class TestKeyChoice(unittest.TestCase):
    def test_roles_are_taken_best_first(self):
        links = [
            {"bus_address": "AA", "role": "scene"},
            {"bus_address": "BB", "role": "on"},
            {"bus_address": "CC", "role": "start_stop"},
        ]
        from nikobus_connect.rgb import RGB_OFF_ROLES, RGB_ON_ROLES
        self.assertEqual(choose_rgb_key(links, RGB_ON_ROLES)["bus_address"], "BB")
        self.assertIsNone(choose_rgb_key(links, RGB_OFF_ROLES))
        self.assertIsNone(choose_rgb_key([], RGB_ON_ROLES))

    def test_links_are_read_from_the_module_entry(self):
        c = _coord(links=LINKS_M19)
        self.assertEqual(len(rgb_links_for(c.dict_module_data, "801d")), 2)
        self.assertEqual(rgb_links_for({}, "801D"), [])
        self.assertEqual(rgb_links_for({"rgb_module": {"801D": {"rgb_links": "junk"}}}, "801D"), [])


class TestApplyLinks(unittest.TestCase):
    def _links(self):
        from custom_components.nikobus.nkbnames import RgbLink
        return (
            RgbLink("801D", "124A36", "1C", "1B1492", 19, "S_DB_DESC_DIMMER_COLOR_M19"),
            RgbLink("801D", "124A36", "1D", "5B1492", 19, "S_DB_DESC_DIMMER_COLOR_M19"),
        )

    def test_a_plate_the_button_store_lacks_still_gives_the_controller_its_keys(self):
        modules = {"801D": {"module_type": "rgb_module"}}
        assert apply_rgb_links(modules, {}, self._links()) == 2
        self.assertEqual([l["role"] for l in modules["801D"]["rgb_links"]], ["start_stop", "off"])

    def test_applying_twice_changes_nothing(self):
        modules = {"801D": {"module_type": "rgb_module"}}
        buttons = {"124A36": {"operation_points": {"1C": {"bus_address": "1B1492"}}}}
        apply_rgb_links(modules, buttons, self._links())
        once = (dict(modules["801D"]), str(buttons))
        apply_rgb_links(modules, buttons, self._links())
        self.assertEqual((dict(modules["801D"]), str(buttons)), once)
        self.assertEqual(len(buttons["124A36"]["operation_points"]["1C"]["linked_modules"][0]["outputs"]), 1)

    def test_an_unreadable_mode_keeps_the_vendor_text_and_no_role(self):
        from custom_components.nikobus.nkbnames import RgbLink
        modules = {"801D": {"module_type": "rgb_module"}}
        apply_rgb_links(modules, {}, (RgbLink("801D", "124A36", "1C", "1B1492", None, "MCF"),))
        link = modules["801D"]["rgb_links"][0]
        self.assertEqual((link["mode"], link["mode_label"], link["role"]), (None, "MCF", None))


class TestKnownIds(unittest.TestCase):
    def test_the_light_and_the_audio_zones_survive_the_orphan_cleanup(self):
        coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
        coord.config_entry = MagicMock()
        coord.config_entry.options = {}
        coord.dict_module_data = {"rgb_module": {"801D": {"module_type": "rgb_module"}}}
        coord.dict_button_data = {"nikobus_button": {"9083CF": {
            "type": "Audio trigger", "audio_function": "M16 (On)", "audio_zone": 2,
            "audio_module_address": "8334",
            "operation_points": {"AUD": {
                "bus_address": "9083CF",
                "linked_modules": [{"module_address": "8334", "outputs": [{"channel": 2}]}],
            }},
        }}}
        coord.dict_scene_data = {}
        coord.cf_storage = None
        known = coord.get_known_entity_unique_ids()
        self.assertIn("nikobus_light_rgb_controller_801D_1", known)
        self.assertIn("nikobus_audio_8334_zone2", known)
