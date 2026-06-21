"""Regression test: CF / light-scene entities must be in the known-id set.

``_async_cleanup_orphan_entities`` removes any Nikobus entity whose
``unique_id`` is not returned by
``NikobusDataCoordinator.get_known_entity_unique_ids``. The scene
platform creates one ``NikobusCFSceneEntity`` per persisted CF with
``unique_id = f"nikobus_cf_{addr.lower()}"``. If those ids are missing
from the known set, the cleanup evicts the scenes right after they are
created — the user sees no scene entities even though ``nikobus.cfs``
is populated (Nikobus-HA, light-scene CF surfacing).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from custom_components.nikobus.coordinator import NikobusDataCoordinator


def _coord_with_cfs(cf_addrs):
    coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    coord.dict_module_data = {}
    coord.dict_button_data = {}
    coord.dict_scene_data = {}
    coord.cf_storage = MagicMock()
    coord.cf_storage.data = {
        "nikobus_cf": {addr: {"pattern": "light_scene", "outputs": []} for addr in cf_addrs}
    }
    return coord


class TestKnownEntityIdsIncludeCFScenes(unittest.TestCase):
    def test_cf_scene_unique_ids_are_known(self):
        coord = _coord_with_cfs(["0D1C9E", "0FFEC8"])

        known = coord.get_known_entity_unique_ids()

        # Must match scene.py: f"nikobus_cf_{addr.lower()}"
        self.assertIn("nikobus_cf_0d1c9e", known)
        self.assertIn("nikobus_cf_0ffec8", known)

    def test_nkb_sourced_scene_unique_ids_are_known(self):
        """.nkb-sourced (source='nkb') scenes live in cf_storage too, so the
        orphan-cleanup must not evict them on the post-import reload — they
        ride the same allowlist as discovered CFs (regression guard, since a
        hard-coded unique_id once slipped the allowlist for the import
        button)."""
        coord = _coord_with_cfs([])
        coord.cf_storage.data = {"nikobus_cf": {
            "AB1234": {"bus_address": "AB1234", "pattern": "nkb_scene",
                       "outputs": [], "source": "nkb", "name": "ShuttersUp"},
        }}
        known = coord.get_known_entity_unique_ids()
        self.assertIn("nikobus_cf_ab1234", known)

    def test_pure_roller_cf_registers_cover_id(self):
        """A pure-roller CF (all shutter members, by mode wording) surfaces
        as a grouped cover (``nikobus_cf_cover_<addr>``), not a broadcast
        scene — the known-id set must match, else orphan cleanup evicts the
        cover right after creation."""
        coord = _coord_with_cfs([])
        coord.cf_storage.data = {"nikobus_cf": {
            "3880CD": {"pattern": "roller_pair", "outputs": [
                {"module_address": "8CF5", "channel": 1, "mode": "M02 (Open)", "t1": "40 s"},
                {"module_address": "8CF5", "channel": 1, "mode": "M03 (Close)", "t1": "40 s"},
            ]},
        }}
        known = coord.get_known_entity_unique_ids()
        self.assertIn("nikobus_cf_cover_3880cd", known)
        self.assertNotIn("nikobus_cf_3880cd", known)

    def test_m01_pure_roller_cf_registers_cover_id(self):
        """A 1-button (M01 toggle) pure-roller CF also becomes a grouped
        cover — register the cover id, not the broadcast scene id."""
        coord = _coord_with_cfs([])
        coord.cf_storage.data = {"nikobus_cf": {
            "3880C8": {"pattern": "roller_pair", "outputs": [
                {"module_address": "C7C1", "channel": 1, "mode": "M01 (Open - stop - close)"},
            ]},
        }}
        known = coord.get_known_entity_unique_ids()
        self.assertIn("nikobus_cf_cover_3880c8", known)
        self.assertNotIn("nikobus_cf_3880c8", known)

    def test_mixed_cf_stays_a_broadcast_scene_id(self):
        """A mixed (switch + roller) CF is not pure-roller, so it stays a
        broadcast CF scene — register the plain scene id, not a cover id."""
        coord = _coord_with_cfs([])
        coord.cf_storage.data = {"nikobus_cf": {
            "3880C9": {"pattern": "nkb_scene", "outputs": [
                {"module_address": "C1C7", "channel": 1, "mode": "M03 (Off + Operating time)"},
                {"module_address": "8CF5", "channel": 2, "mode": "M03 (Close)"},
            ]},
        }}
        known = coord.get_known_entity_unique_ids()
        self.assertIn("nikobus_cf_3880c9", known)
        self.assertNotIn("nikobus_cf_cover_3880c9", known)

    def test_no_cfs_is_safe(self):
        coord = _coord_with_cfs([])
        known = coord.get_known_entity_unique_ids()
        self.assertFalse(any(k.startswith("nikobus_cf_") for k in known))

    def test_missing_cf_storage_is_safe(self):
        coord = _coord_with_cfs([])
        coord.cf_storage = None
        # Should not raise.
        known = coord.get_known_entity_unique_ids()
        self.assertIsInstance(known, set)


class TestKnownEntityIdsIncludeBridgeButtons(unittest.TestCase):
    """The three hub config buttons must be in the known-id set, else the
    orphan cleanup evicts them right after the button platform creates them
    (the import-names button regressed exactly this way: created then
    immediately removed at startup)."""

    def test_all_three_bridge_buttons_are_known(self):
        coord = _coord_with_cfs([])
        known = coord.get_known_entity_unique_ids()
        self.assertIn("nikobus_pc_link_inventory_button", known)
        self.assertIn("nikobus_module_scan_button", known)
        self.assertIn("nikobus_import_nkb_names_button", known)


if __name__ == "__main__":
    unittest.main()
