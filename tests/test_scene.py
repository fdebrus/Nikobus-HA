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


class TestSceneActivationErrors(unittest.TestCase):
    """A bus failure during a software-scene activation surfaces as a
    translated HomeAssistantError, not the raw library exception."""

    def test_activate_translates_bus_error(self):
        from homeassistant.exceptions import HomeAssistantError

        coord = MagicMock()
        coord.get_module_type.return_value = "switch_module"
        coord.nikobus_module_states = {"C1C7": bytearray(12)}
        coord.get_module_channel_count.return_value = 6
        coord.async_event_handler = AsyncMock()
        coord.api.set_output_states_for_module = AsyncMock(
            side_effect=RuntimeError("bus down")
        )
        e = NikobusSceneEntity(coord, {
            "id": "s1",
            "channels": [{"module_id": "C1C7", "channel": 1, "state": "on"}],
            "feedback_led": None,
        })
        e.name = "Test Scene"  # Entity.name isn't provided by the test stubs
        with self.assertRaises(HomeAssistantError) as cm:
            _run(e.async_activate())
        self.assertEqual(cm.exception.translation_key, "communication_error")
        self.assertIsInstance(cm.exception.__cause__, RuntimeError)


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


class TestCFSceneAttributes(unittest.TestCase):
    def _make(self, outputs):
        coord = MagicMock()
        coord.address_label = lambda a: f"mod_{a} ({a})" if a else ""
        coord.get_button_context = MagicMock(return_value=None)
        e = NikobusCFSceneEntity(
            coord,
            bus_address="DE4E2C",
            cf_config={"pattern": "light_scene", "outputs": outputs},
        )
        return e, coord

    def test_human_outputs_label_module_and_level(self):
        e, _ = self._make([
            {"module_address": "0E6C", "channel": 1,
             "mode": "M04 (Light scene on)", "t1": "6%"},
            {"module_address": "8394", "channel": 1, "mode": "M02 (Open)", "t1": None},
        ])
        outs = e._human_outputs()
        self.assertEqual(
            outs[0],
            {"module": "mod_0E6C (0E6C)", "channel": 1,
             "action": "M04 (Light scene on)", "level": "6%"},
        )
        # no level key when t1 is empty
        self.assertEqual(
            outs[1],
            {"module": "mod_8394 (8394)", "channel": 1, "action": "M02 (Open)"},
        )

    def test_triggered_by_falls_back_to_address(self):
        e, coord = self._make([])
        coord.get_button_context.return_value = None
        self.assertEqual(e._trigger_labels(), ["DE4E2C"])

    def test_triggered_by_uses_button_name(self):
        e, coord = self._make([])
        coord.get_button_context.return_value = (
            "0D1C80", "IR:30B", {"description": "IR code 30B"},
            {"type": "Push button with IR receiver"},
        )
        self.assertEqual(e._trigger_labels(), ["IR 30B on 0D1C80 (DE4E2C)"])

    def test_multiple_triggers_listed(self):
        coord = MagicMock()
        coord.address_label = lambda a: a
        coord.get_button_context = MagicMock(return_value=None)
        e = NikobusCFSceneEntity(
            coord,
            bus_address="8B7086",
            cf_config={
                "pattern": "light_scene",
                "outputs": [],
                "triggered_by": ["8B7086", "DE4E2C"],
            },
        )
        self.assertEqual(e._triggered_by, ["8B7086", "DE4E2C"])
        self.assertEqual(e._trigger_labels(), ["8B7086", "DE4E2C"])

    def test_handle_trigger_fires_on_secondary_trigger(self):
        coord = MagicMock()
        coord.address_label = lambda a: a
        coord.get_button_context = MagicMock(return_value=None)
        e = NikobusCFSceneEntity(
            coord,
            bus_address="8B7086",
            cf_config={"pattern": "light_scene", "outputs": [],
                       "triggered_by": ["8B7086", "DE4E2C"]},
        )
        e.hass = MagicMock()
        e.entity_id = "scene.x"
        # Routed by address, so any delivery is one of this scene's triggers.
        e._handle_trigger({"address": "DE4E2C"})
        e.hass.bus.async_fire.assert_called_once()
        _, payload = e.hass.bus.async_fire.call_args.args
        self.assertEqual(payload["address"], "DE4E2C")

    def test_handle_trigger_fires_event_on_match(self):
        e, _ = self._make([{"module_address": "0E6C", "channel": 1,
                            "mode": "M04 (Light scene on)"}])
        e.hass = MagicMock()
        e.entity_id = "scene.nikobus_scene_de4e2c"
        e._handle_trigger({"address": "DE4E2C"})
        e.hass.bus.async_fire.assert_called_once()
        name, payload = e.hass.bus.async_fire.call_args.args
        self.assertEqual(name, "nikobus_scene_activated")
        self.assertEqual(payload["address"], "DE4E2C")
        self.assertEqual(payload["member_count"], 1)

    def test_subscribes_to_each_trigger_signal(self):
        from unittest.mock import patch

        from custom_components.nikobus.const import press_signal

        coord = MagicMock()
        coord.address_label = lambda a: a
        coord.get_button_context = MagicMock(return_value=None)
        e = NikobusCFSceneEntity(
            coord, bus_address="8B7086",
            cf_config={"pattern": "light_scene", "outputs": [],
                       "triggered_by": ["8B7086", "DE4E2C"]},
        )
        e.hass = MagicMock()
        with patch(
            "custom_components.nikobus.scene.async_dispatcher_connect",
            return_value=lambda: None,
        ) as conn:
            _run(e.async_added_to_hass())
        signals = [c.args[1] for c in conn.call_args_list]
        self.assertIn(press_signal("8B7086"), signals)
        self.assertIn(press_signal("DE4E2C"), signals)


class TestCoordinatorSceneHelpers(unittest.TestCase):
    def _coord(self):
        from custom_components.nikobus.coordinator import NikobusDataCoordinator
        return NikobusDataCoordinator.__new__(NikobusDataCoordinator)

    def test_get_scene_for_address(self):
        c = self._coord()
        c.cf_storage = MagicMock()
        c.cf_storage.data = {"nikobus_cf": {"DE4E2C": {"pattern": "light_scene",
                                                       "outputs": [1, 2, 3]}}}
        self.assertEqual(c.get_scene_for_address("de4e2c")["outputs"], [1, 2, 3])
        self.assertIsNone(c.get_scene_for_address("FFFFFF"))
        c.cf_storage = None
        self.assertIsNone(c.get_scene_for_address("DE4E2C"))

    def test_get_scene_for_address_matches_secondary_trigger(self):
        c = self._coord()
        c.cf_storage = MagicMock()
        c.cf_storage.data = {"nikobus_cf": {"8B7086": {
            "pattern": "light_scene", "outputs": [1],
            "triggered_by": ["8B7086", "DE4E2C"]}}}
        # canonical key resolves
        self.assertIsNotNone(c.get_scene_for_address("8B7086"))
        # a non-canonical trigger resolves to the same scene
        self.assertEqual(
            c.get_scene_for_address("de4e2c")["triggered_by"],
            ["8B7086", "DE4E2C"],
        )
        self.assertIsNone(c.get_scene_for_address("FFFFFF"))

    def test_address_label_fallback_without_device(self):
        c = self._coord()
        c.hass = None
        self.assertEqual(c.address_label("0e6c"), "0E6C")
        self.assertEqual(c.address_label(""), "")

    def test_address_label_uses_device_name(self):
        from unittest.mock import patch
        c = self._coord()
        c.hass = MagicMock()
        dev = MagicMock()
        dev.name_by_user = None
        dev.name = "dimmer_module_d1"
        reg = MagicMock()
        reg.async_get_device.return_value = dev
        with patch("custom_components.nikobus.coordinator.dr.async_get",
                   return_value=reg):
            self.assertEqual(c.address_label("0E6C"), "dimmer_module_d1 (0E6C)")


if __name__ == "__main__":
    unittest.main()
