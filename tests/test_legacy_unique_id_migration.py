"""Upgrading from 0.6.x keeps the entities (Nikobus-HA #552).

0.6.x / 0.7.x keyed entities ``nikobus_<addr>_<ch>`` (dimmer light, relay
switch, cover — the platform says which), ``nikobus_switch_<addr>_<ch>``
(roller used as a switch) and ``nikobus_button_sensor_<addr>``. 0.8.0 moved
to the current format without re-keying, so every entity was replaced.
The version-1 config-entry migration now re-keys them first.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.nikobus import _migrate_legacy_unique_ids, async_migrate_entry
from custom_components.nikobus.router import legacy_unique_id_replacement


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestMapping(unittest.TestCase):
    def test_the_formats_from_the_report(self):
        cases = {
            ("light", "nikobus_E520_1"): "nikobus_light_dimmer_light_E520_1",
            ("switch", "nikobus_91AD_1"): "nikobus_switch_relay_switch_91AD_1",
            ("cover", "nikobus_8B9C_3"): "nikobus_cover_cover_8B9C_3",
            ("switch", "nikobus_switch_8B9C_3"): "nikobus_switch_cover_binary_8B9C_3",
            ("binary_sensor", "nikobus_button_sensor_A3EE42"): "nikobus_button_A3EE42",
        }
        for (platform, old), new in cases.items():
            self.assertEqual(legacy_unique_id_replacement(platform, old), new, old)

    def test_addresses_are_upper_cased(self):
        self.assertEqual(legacy_unique_id_replacement("switch", "nikobus_91ad_12"), "nikobus_switch_relay_switch_91AD_12")
        self.assertEqual(legacy_unique_id_replacement("binary_sensor", "nikobus_button_sensor_a3ee42"), "nikobus_button_A3EE42")

    def test_current_and_unrelated_ids_are_left_alone(self):
        for platform, uid in (
            ("light", "nikobus_light_dimmer_light_E520_1"),
            ("switch", "nikobus_switch_relay_switch_91AD_1"),
            ("switch", "nikobus_switch_cover_binary_8B9C_3"),
            ("binary_sensor", "nikobus_button_A3EE42"),
            ("button", "nikobus_push_button_A3EE42"),
            ("scene", "nikobus_scene_1"),
            ("sensor", "nikobus_E520_1"),  # no channel entity on that platform
            ("light", "nikobus_E520_123"),
            ("switch", "nikobus_E52_1"),
        ):
            self.assertIsNone(legacy_unique_id_replacement(platform, uid), uid)


def _registry(entries, taken=()):
    reg = MagicMock()
    reg.async_get_entity_id = MagicMock(
        side_effect=lambda domain, platform, uid: f"{domain}.taken" if uid in taken else None
    )
    return reg, [SimpleNamespace(**e) for e in entries]


ENTRIES = [
    {"entity_id": "light.nbd_4_1_salon", "domain": "light", "platform": "nikobus", "unique_id": "nikobus_E520_1"},
    {"entity_id": "switch.nbs_12_b_1_jarne", "domain": "switch", "platform": "nikobus", "unique_id": "nikobus_91AD_1"},
    {"entity_id": "binary_sensor.bp1", "domain": "binary_sensor", "platform": "nikobus", "unique_id": "nikobus_button_sensor_A3EE42"},
    {"entity_id": "button.bp1", "domain": "button", "platform": "nikobus", "unique_id": "nikobus_push_button_A3EE42"},
    {"entity_id": "light.wrapped", "domain": "light", "platform": "switch_as_x", "unique_id": "nikobus_91AD_1"},
]


class TestMigration(unittest.TestCase):
    def _migrate(self, reg, entries):
        entry = SimpleNamespace(entry_id="E1")
        with patch("custom_components.nikobus.er.async_get", return_value=reg), \
             patch("custom_components.nikobus.er.async_entries_for_config_entry", return_value=entries):
            return _migrate_legacy_unique_ids(MagicMock(), entry)

    def test_old_entities_are_re_keyed_in_place(self):
        reg, entries = _registry(ENTRIES)
        self.assertEqual(self._migrate(reg, entries), 3)
        calls = {c.args[0]: c.kwargs["new_unique_id"] for c in reg.async_update_entity.call_args_list}
        self.assertEqual(calls, {
            "light.nbd_4_1_salon": "nikobus_light_dimmer_light_E520_1",
            "switch.nbs_12_b_1_jarne": "nikobus_switch_relay_switch_91AD_1",
            "binary_sensor.bp1": "nikobus_button_A3EE42",
        })

    def test_an_id_already_taken_is_skipped_not_fatal(self):
        reg, entries = _registry(ENTRIES, taken={"nikobus_light_dimmer_light_E520_1"})
        self.assertEqual(self._migrate(reg, entries), 2)
        self.assertNotIn("light.nbd_4_1_salon", [c.args[0] for c in reg.async_update_entity.call_args_list])

    def test_the_version_1_migration_runs_it(self):
        hass = MagicMock()
        entry = SimpleNamespace(entry_id="E1", version=1, minor_version=1, options={})
        with patch("custom_components.nikobus._migrate_legacy_unique_ids") as migrate:
            self.assertTrue(_run(async_migrate_entry(hass, entry)))
        migrate.assert_called_once_with(hass, entry)
        hass.config_entries.async_update_entry.assert_called_once()

    def test_a_version_2_entry_is_not_touched(self):
        from custom_components.nikobus.const import CONFIG_ENTRY_VERSION
        entry = SimpleNamespace(entry_id="E1", version=CONFIG_ENTRY_VERSION, minor_version=1, options={})
        with patch("custom_components.nikobus._migrate_legacy_unique_ids") as migrate:
            self.assertTrue(_run(async_migrate_entry(MagicMock(), entry)))
        migrate.assert_not_called()
