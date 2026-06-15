"""Entity-creation tests for the output platforms' ``async_setup_entry``.

Phase-1 gold matrix: feed a realistic ``dict_module_data`` through the
real router (``get_routing``/``build_routing``) and assert each
platform materialises the right entities — count, class, address,
channel and unique_id uniqueness. Device-registry writes are patched
out (``register_output_module_devices``).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

import importlib

# ``from custom_components.nikobus import light`` would resolve to the
# *homeassistant.components.light* stub: the package __init__ imports the
# HA platform modules under the same names, shadowing the submodules.
# ``import_module`` returns the real submodule regardless.
cover_platform = importlib.import_module("custom_components.nikobus.cover")
light_platform = importlib.import_module("custom_components.nikobus.light")
scene_platform = importlib.import_module("custom_components.nikobus.scene")
switch_platform = importlib.import_module("custom_components.nikobus.switch")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_MODULE_DATA = {
    "dimmer_module": {
        "D1A2": {
            "description": "Dimmer salon",
            "model": "05-007-02",
            "channels": [
                {"description": "Spots salon"},
                {"description": "Suspension"},
            ],
        }
    },
    "switch_module": {
        "8110": {
            "description": "Relais RDC",
            "model": "05-000-02",
            "channels": [
                {"description": "Prise TV"},
                {"description": "not_in_use 2"},  # legacy skip convention
                {"description": "Cuisine", "entity_type": "disabled"},  # UI skip
            ],
        }
    },
    "roller_module": {
        "C9A5": {
            "description": "Volets étage",
            "model": "05-001-02",
            "channels": [{"description": "Volet chambre"}],
        }
    },
}


def _entry_and_coord():
    coord = MagicMock()
    coord.dict_module_data = _MODULE_DATA
    entry = MagicMock()
    entry.entry_id = "entry_test"
    entry.runtime_data = coord
    hass = MagicMock()
    hass.data = {}
    return hass, entry, coord


class TestOutputPlatformSetup(unittest.TestCase):
    def _setup(self, platform):
        hass, entry, coord = _entry_and_coord()
        added: list = []
        with patch.object(
            platform, "register_output_module_devices", MagicMock()
        ):
            _run(
                platform.async_setup_entry(
                    hass, entry, lambda ents, **kw: added.extend(ents)
                )
            )
        return added

    def test_light_platform_creates_dimmer_entities(self):
        entities = self._setup(light_platform)
        self.assertEqual(len(entities), 2)
        for ent, channel in zip(entities, (1, 2)):
            self.assertIsInstance(ent, light_platform.NikobusDimmerEntity)
            self.assertEqual(ent._address, "D1A2")
            self.assertEqual(ent._channel, channel)
        # unique_ids must exist and be distinct.
        uids = [e._attr_unique_id for e in entities]
        self.assertEqual(len(set(uids)), 2)
        self.assertTrue(all(uid for uid in uids))

    def test_switch_platform_skips_unused_channels(self):
        entities = self._setup(switch_platform)
        # 3 catalogued channels, but "not_in_use" + "disabled" are skipped.
        relays = [
            e
            for e in entities
            if getattr(e, "_address", None) == "8110"
        ]
        self.assertEqual(len(relays), 1)
        self.assertEqual(relays[0]._channel, 1)

    def test_cover_platform_creates_cover(self):
        entities = self._setup(cover_platform)
        covers = [e for e in entities if getattr(e, "_address", None) == "C9A5"]
        self.assertEqual(len(covers), 1)
        self.assertEqual(covers[0]._channel, 1)

    def test_roller_pair_cf_becomes_grouped_cover(self):
        """A roller_pair CF surfaces as a grouped NikobusCFCoverEntity in the
        cover platform (not a dead scene)."""
        hass, entry, coord = _entry_and_coord()
        coord.cf_storage.data = {
            "nikobus_cf": {
                "3880CD": {
                    "pattern": "roller_pair",
                    "outputs": [
                        {"module_address": "8CF5", "channel": 1, "mode": "M02", "t1": "40 s"},
                        {"module_address": "8CF5", "channel": 1, "mode": "M03", "t1": "40 s"},
                    ],
                },
                # A light_scene CF must NOT become a cover.
                "0D1C9E": {"pattern": "light_scene", "outputs": []},
            }
        }
        added: list = []
        with patch.object(
            cover_platform, "register_output_module_devices", MagicMock()
        ):
            _run(
                cover_platform.async_setup_entry(
                    hass, entry, lambda ents, **kw: added.extend(ents)
                )
            )
        cf_covers = [
            e for e in added if isinstance(e, cover_platform.NikobusCFCoverEntity)
        ]
        self.assertEqual(len(cf_covers), 1)
        self.assertEqual(cf_covers[0]._bus_address, "3880CD")
        self.assertEqual(cf_covers[0]._attr_unique_id, "nikobus_cf_cover_3880cd")

    def test_routing_is_cached_per_entry(self):
        hass, entry, coord = _entry_and_coord()
        added: list = []
        with patch.object(
            light_platform, "register_output_module_devices", MagicMock()
        ):
            _run(
                light_platform.async_setup_entry(
                    hass, entry, lambda ents, **kw: added.extend(ents)
                )
            )
        from custom_components.nikobus.router import _ROUTING_CACHE_KEY

        self.assertIn(_ROUTING_CACHE_KEY, hass.data["nikobus"]["entry_test"])


class TestScenePlatformSetup(unittest.TestCase):
    def test_user_and_cf_scenes_created(self):
        coord = MagicMock()
        coord.dict_scene_data = {
            "scene": [
                {"id": "sc1", "description": "Soirée", "channels": []},
                {"description": "sans id — ignorée"},
            ]
        }
        coord.cf_storage.data = {
            "nikobus_cf": {
                "3841AA": {
                    "pattern": "switch_pair",
                    "outputs": [
                        {"module_address": "8110", "channel": 1, "mode": "M01"}
                    ],
                    "triggered_by": ["3841AA"],
                },
                # roller_pair CFs are handled by the cover platform now —
                # the scene platform must NOT surface them.
                "3880CD": {
                    "pattern": "roller_pair",
                    "outputs": [
                        {"module_address": "8CF5", "channel": 1, "mode": "M02", "t1": "40 s"},
                        {"module_address": "8CF5", "channel": 1, "mode": "M03", "t1": "40 s"},
                    ],
                },
                "garbage": "not-a-dict",  # ignored
            }
        }
        entry = MagicMock()
        entry.runtime_data = coord
        added: list = []
        _run(
            scene_platform.async_setup_entry(
                MagicMock(), entry, lambda ents, **kw: added.extend(ents)
            )
        )
        user_scenes = [
            e for e in added if isinstance(e, scene_platform.NikobusSceneEntity)
        ]
        cf_scenes = [
            e for e in added if isinstance(e, scene_platform.NikobusCFSceneEntity)
        ]
        self.assertEqual(len(user_scenes), 1)
        self.assertEqual(len(cf_scenes), 1)  # switch_pair only; roller_pair skipped
        self.assertEqual(cf_scenes[0]._bus_address, "3841AA")
        self.assertNotIn("3880CD", [e._bus_address for e in cf_scenes])
