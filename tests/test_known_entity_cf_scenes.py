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


if __name__ == "__main__":
    unittest.main()
