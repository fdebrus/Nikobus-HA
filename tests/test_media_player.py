"""Audio zones: one media player per zone of a 05-205.

The module answers no state query, so a zone's state is whatever was
last commanded — by Home Assistant or by a wall key, both of which the
PC-Link relays. The trigger addresses come from the module's own link
table, as read by the library and filed in the button store.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.media_player import NikobusAudioZone
from custom_components.nikobus.router import audio_zones

# One zone of the validating install (module 8334, zone 2).
ZONE_2 = {
    "M03 (Source 1)": "B083CF",
    "M04 (Source 2)": "F083CF",
    "M05 (Source 3)": "3083CF",
    "M06 (Source 4)": "7083CF",
    "M13 (Volume up)": "1083CF",
    "M14 (Volume down)": "5083CF",
    "M16 (On)": "9083CF",
    "M17 (Off)": "D083CF",
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _zone(functions=None):
    coordinator = MagicMock()
    coordinator.async_send_button_press = AsyncMock()
    coordinator.address_label = MagicMock(return_value="Audio link (8334)")
    entity = NikobusAudioZone(coordinator, "8334", 2, dict(functions or ZONE_2))
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


def _button_store():
    """The store shape the library's merge produces for audio triggers."""
    return {
        address: {
            "audio_function": function,
            "audio_zone": 2,
            "operation_points": {
                "AUD": {
                    "bus_address": address,
                    "linked_modules": [{"module_address": "8334", "outputs": [{"channel": 2}]}],
                }
            },
        }
        for function, address in ZONE_2.items()
    }


class TestGrouping(unittest.TestCase):
    def test_triggers_group_by_module_and_zone(self):
        grouped = audio_zones(_button_store())
        self.assertEqual(list(grouped), [("8334", 2)])
        self.assertEqual(grouped[("8334", 2)], ZONE_2)

    def test_all_zone_triggers_land_under_zone_zero(self):
        store = {
            "8483CF": {
                "audio_function": "M11 (Source toggle)",
                "audio_zone": None,
                "operation_points": {
                    "AUD": {"linked_modules": [{"module_address": "8334"}]}
                },
            }
        }
        self.assertEqual(list(audio_zones(store)), [("8334", 0)])

    def test_ordinary_buttons_are_ignored(self):
        store = {"0D1C80": {"operation_points": {"1A": {"bus_address": "004E2C"}}}}
        self.assertEqual(audio_zones(store), {})


class TestZoneEntity(unittest.TestCase):
    def test_identity_and_sources(self):
        entity, _ = _zone()
        self.assertEqual(entity._attr_unique_id, "nikobus_audio_8334_zone2")
        self.assertEqual(entity._attr_name, "Zone 2")
        self.assertEqual(entity.source_list, ["Source 1", "Source 2", "Source 3", "Source 4"])

    def test_state_is_unknown_until_something_drives_the_zone(self):
        entity, _ = _zone()
        self.assertIsNone(entity.state)

    def test_turn_on_presses_the_zone_trigger(self):
        entity, coordinator = _zone()
        _run(entity.async_turn_on())
        coordinator.async_send_button_press.assert_awaited_once_with("9083CF")
        self.assertEqual(entity.state, MediaPlayerState.ON)

    def test_turn_off_presses_the_zone_trigger(self):
        entity, coordinator = _zone()
        _run(entity.async_turn_off())
        coordinator.async_send_button_press.assert_awaited_once_with("D083CF")
        self.assertEqual(entity.state, MediaPlayerState.OFF)

    def test_volume_steps_press_their_triggers(self):
        entity, coordinator = _zone()
        _run(entity.async_volume_up())
        _run(entity.async_volume_down())
        self.assertEqual(
            [c.args[0] for c in coordinator.async_send_button_press.await_args_list],
            ["1083CF", "5083CF"],
        )

    def test_select_source_presses_and_records_it(self):
        entity, coordinator = _zone()
        _run(entity.async_select_source("Source 3"))
        coordinator.async_send_button_press.assert_awaited_once_with("3083CF")
        self.assertEqual(entity.source, "Source 3")
        self.assertEqual(entity.state, MediaPlayerState.ON)

    def test_unknown_source_is_refused(self):
        entity, coordinator = _zone()
        with self.assertRaises(HomeAssistantError):
            _run(entity.async_select_source("Source 9"))
        coordinator.async_send_button_press.assert_not_awaited()

    def test_a_function_the_zone_lacks_is_refused(self):
        entity, coordinator = _zone({"M16 (On)": "9083CF"})
        with self.assertRaises(HomeAssistantError):
            _run(entity.async_turn_off())
        coordinator.async_send_button_press.assert_not_awaited()

    def test_a_failed_press_surfaces_as_an_ha_error(self):
        entity, coordinator = _zone()
        coordinator.async_send_button_press.side_effect = RuntimeError("bus down")
        with self.assertRaises(HomeAssistantError) as caught:
            _run(entity.async_turn_on())
        self.assertEqual(caught.exception.translation_key, "communication_error")
        self.assertIsNone(entity.state)

    def test_a_wall_key_on_the_same_zone_updates_the_entity(self):
        """The PC-Link relays the wall press, so the two stay in step."""
        entity, _ = _zone()
        entity._handle_press({"address": "9083CF"})
        self.assertEqual(entity.state, MediaPlayerState.ON)
        entity._handle_press({"address": "7083CF"})
        self.assertEqual(entity.source, "Source 4")
        entity._handle_press({"address": "D083CF"})
        self.assertEqual(entity.state, MediaPlayerState.OFF)

    def test_a_press_for_another_zone_is_ignored(self):
        entity, _ = _zone()
        entity._handle_press({"address": "8083CF"})  # zone 1 On
        self.assertIsNone(entity.state)

    def test_attributes_expose_the_wiring(self):
        entity, _ = _zone()
        attrs = entity.extra_state_attributes
        self.assertEqual(attrs["zone"], 2)
        self.assertEqual(attrs["nikobus_address"], "8334")
        self.assertEqual(attrs["trigger_addresses"]["M16 (On)"], "9083CF")


class TestPlatformSetup(unittest.TestCase):
    def test_one_entity_per_zone_and_none_for_the_all_zones_bucket(self):
        from custom_components.nikobus import media_player as platform

        store = _button_store()
        store["8483CF"] = {
            "audio_function": "M11 (Source toggle)",
            "audio_zone": None,
            "operation_points": {"AUD": {"linked_modules": [{"module_address": "8334"}]}},
        }
        coordinator = MagicMock()
        coordinator.dict_button_data = {"nikobus_button": store}
        coordinator.address_label = MagicMock(return_value="Audio link (8334)")
        entry, hass = MagicMock(), MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        with patch.object(platform, "NikobusEntity", NikobusAudioZone.__mro__[1]):
            _run(platform.async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents)))
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]._zone, 2)


if __name__ == "__main__":
    unittest.main()
