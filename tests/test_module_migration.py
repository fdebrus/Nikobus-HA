"""Tests for the one-shot legacy module-config migration.

Covers the three scenarios from the migration spec:

  * empty Store + present JSON   → migrated Store, JSON renamed to .migrated.bak
  * empty Store + no JSON        → Store stays empty, no side effects
  * populated Store + present JSON → migration skipped, JSON untouched
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

# ---------------------------------------------------------------------------
# Replace the ``aiofiles`` stub with a real implementation that uses asyncio
# run-in-executor wrappers. The shared conftest stubs aiofiles with an
# AsyncMock, which doesn't actually read files.
# ---------------------------------------------------------------------------


class _AsyncFileContext:
    def __init__(self, path: str, mode: str):
        self._path = path
        self._mode = mode
        self._fh = None

    async def __aenter__(self):
        self._fh = open(self._path, self._mode)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._fh is not None:
            self._fh.close()

    async def read(self) -> str:
        return await asyncio.get_running_loop().run_in_executor(None, self._fh.read)

    async def write(self, data: str) -> int:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._fh.write, data
        )


def _real_aiofiles_open(path: str, mode: str = "r"):
    return _AsyncFileContext(path, mode)


_aiofiles_stub = sys.modules.get("aiofiles")
if _aiofiles_stub is not None:
    _aiofiles_stub.open = _real_aiofiles_open  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Load the migration module directly; it imports from .nkbstorage.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
COMP = ROOT / "custom_components" / "nikobus"


def _load(name, path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = ".".join(name.split(".")[:-1])
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Ensure nkbstorage is loaded (conftest loads it indirectly via coordinator).
if "custom_components.nikobus.nkbstorage" not in sys.modules:
    _load("custom_components.nikobus.nkbstorage", COMP / "nkbstorage.py")

nkbmigration = _load(
    "custom_components.nikobus.nkbmigration", COMP / "nkbmigration.py"
)
nkbstorage = sys.modules["custom_components.nikobus.nkbstorage"]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, config_dir: str):
        self.config_dir = config_dir

    def path(self, filename: str) -> str:
        return os.path.join(self.config_dir, filename)


class _FakeHass:
    def __init__(self, config_dir: str):
        self.config = _FakeConfig(config_dir)


class _InMemoryModuleStore:
    """Drop-in for ``NikobusModuleStorage`` that skips the HA Store layer."""

    def __init__(self, initial: dict | None = None):
        self._data = initial if initial is not None else {"nikobus_module": {}}
        self.saved_snapshots: list[dict] = []

    async def async_load(self) -> dict:
        return self._data

    async def async_save(self) -> None:
        # Deep-copy so the snapshot reflects the state at save-time.
        self.saved_snapshots.append(
            json.loads(json.dumps(self._data))
        )

    @property
    def data(self) -> dict:
        return self._data

    @property
    def is_empty(self) -> bool:
        return not bool(self._data.get("nikobus_module"))


# ---------------------------------------------------------------------------
# convert_legacy_to_flat — pure unit tests
# ---------------------------------------------------------------------------


class TestConvertLegacyToFlat(unittest.TestCase):
    def test_list_form_switch_module(self):
        legacy = {
            "switch_module": [
                {
                    "description": "Switch S1",
                    "model": "05-000-02",
                    "address": "C9A5",
                    "channels": [
                        {"description": "Kitchen", "led_on": "1234AB", "led_off": ""},
                        {"description": "Pantry"},
                    ],
                }
            ]
        }
        flat = nkbmigration.convert_legacy_to_flat(legacy)
        self.assertIn("C9A5", flat)
        entry = flat["C9A5"]
        self.assertEqual(entry["module_type"], "switch_module")
        self.assertEqual(entry["description"], "Switch S1")
        self.assertEqual(entry["model"], "05-000-02")
        self.assertEqual(len(entry["channels"]), 2)
        self.assertEqual(entry["channels"][0]["description"], "Kitchen")
        self.assertEqual(entry["channels"][0]["led_on"], "1234AB")
        self.assertNotIn("led_off", entry["channels"][0])
        # Legacy files have no entity_type — migration must not invent one.
        self.assertNotIn("entity_type", entry["channels"][0])

    def test_roller_splits_operation_time(self):
        legacy = {
            "roller_module": [
                {
                    "description": "R1",
                    "address": "9105",
                    "channels": [
                        {"description": "Living", "operation_time": "40"},
                        {"description": "Bedroom", "operation_time_up": "25"},
                    ],
                }
            ]
        }
        flat = nkbmigration.convert_legacy_to_flat(legacy)
        ch0 = flat["9105"]["channels"][0]
        self.assertEqual(ch0["operation_time_up"], "40")
        self.assertEqual(ch0["operation_time_down"], "40")
        ch1 = flat["9105"]["channels"][1]
        self.assertEqual(ch1["operation_time_up"], "25")
        self.assertNotIn("operation_time_down", ch1)

    def test_dict_form_preserved(self):
        legacy = {
            "dimmer_module": {
                "0E6C": {
                    "description": "D1",
                    "channels": [{"description": "Hallway"}],
                }
            }
        }
        flat = nkbmigration.convert_legacy_to_flat(legacy)
        self.assertIn("0E6C", flat)
        self.assertEqual(flat["0E6C"]["module_type"], "dimmer_module")

    def test_non_dict_input_returns_empty(self):
        self.assertEqual(nkbmigration.convert_legacy_to_flat(None), {})  # type: ignore[arg-type]
        self.assertEqual(nkbmigration.convert_legacy_to_flat("oops"), {})  # type: ignore[arg-type]

    def test_skips_entries_without_address(self):
        legacy = {
            "switch_module": [
                {"description": "Orphan"},  # no address
                {"description": "Keeper", "address": "ABCD", "channels": []},
            ]
        }
        flat = nkbmigration.convert_legacy_to_flat(legacy)
        self.assertEqual(list(flat.keys()), ["ABCD"])


# ---------------------------------------------------------------------------
# async_migrate_legacy_module_config — integration tests
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestMigrationScenarios(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = self._tmp.name
        self.hass = _FakeHass(self.config_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_legacy(self, payload: dict) -> str:
        path = os.path.join(self.config_dir, "nikobus_module_config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    # -- Scenario A: empty Store + present JSON --------------------------

    def test_migrates_when_store_empty_and_file_exists(self):
        legacy_path = self._write_legacy({
            "switch_module": [
                {"description": "S1", "model": "05-000-02", "address": "C9A5",
                 "channels": [{"description": "Ch1"}]},
            ],
            "roller_module": [
                {"description": "R1", "address": "9105",
                 "channels": [{"description": "Living", "operation_time": "40"}]},
            ],
        })
        store = _InMemoryModuleStore()

        migrated = _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertTrue(migrated)
        self.assertIn("C9A5", store.data["nikobus_module"])
        self.assertIn("9105", store.data["nikobus_module"])
        self.assertEqual(
            store.data["nikobus_module"]["9105"]["channels"][0]["operation_time_up"],
            "40",
        )
        # Source renamed, not deleted.
        self.assertFalse(os.path.exists(legacy_path))
        self.assertTrue(
            os.path.exists(legacy_path + ".migrated"),
            "Source file should be renamed to .migrated",
        )
        # Exactly one save call — no double-write.
        self.assertEqual(len(store.saved_snapshots), 1)

    # -- Scenario B: empty Store + no JSON -------------------------------

    def test_no_op_when_store_empty_and_no_file(self):
        store = _InMemoryModuleStore()

        migrated = _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertFalse(migrated)
        self.assertEqual(store.data, {"nikobus_module": {}})
        self.assertEqual(store.saved_snapshots, [])
        self.assertEqual(os.listdir(self.config_dir), [])

    # -- Scenario C: populated Store + present JSON ----------------------

    # -- Scenario C: populated Store + present JSON ----------------------
    #
    # Per the discussion on the in-place rename gap: when discovery has
    # already populated the Store, we no longer bail. Instead we overlay
    # user-editable fields from the legacy file onto matching entries
    # (so descriptions, LED triggers, travel times, entity_type carry
    # over) without touching discovery-owned fields (model, module_type,
    # channel count). The source file is still renamed afterwards so it
    # stops being authoritative.

    def test_overlays_module_description_onto_populated_store(self):
        legacy_path = self._write_legacy({
            "switch_module": [
                {"description": "Living Room Lights", "model": "05-000-02",
                 "address": "C9A5", "channels": []}
            ]
        })
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "C9A5": {
                    "module_type": "switch_module",
                    "description": "Compact Switch Module (C9A5)",  # discovery default
                    "model": "05-002-02",  # discovery sets this
                    "channels": [],
                }
            }
        })

        migrated = _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertTrue(migrated)
        entry = store.data["nikobus_module"]["C9A5"]
        # User-editable field overlaid:
        self.assertEqual(entry["description"], "Living Room Lights")
        # Discovery-owned field NOT overwritten by legacy:
        self.assertEqual(entry["model"], "05-002-02")
        # File renamed:
        self.assertFalse(os.path.exists(legacy_path))
        self.assertTrue(os.path.exists(legacy_path + ".migrated"))

    def test_overlays_channel_user_fields_onto_populated_store(self):
        legacy_path = self._write_legacy({
            "switch_module": [{
                "description": "Kitchen", "address": "AABB",
                "channels": [
                    {"description": "Counter", "entity_type": "light",
                     "led_on": "1A2B3C", "led_off": "4D5E6F"},
                    {"description": "Pendant"},
                ],
            }],
            "roller_module": [{
                "description": "Office Blinds", "address": "CCDD",
                "channels": [
                    {"description": "South window", "operation_time": "30"},
                ],
            }],
        })
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "AABB": {
                    "module_type": "switch_module",
                    "description": "Compact Switch Module",
                    "channels": [
                        {"description": "Channel 1"},  # discovery default
                        {"description": "Channel 2"},
                    ],
                },
                "CCDD": {
                    "module_type": "roller_module",
                    "description": "Roller Module",
                    "channels": [{"description": "Channel 1"}],
                },
            }
        })

        migrated = _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertTrue(migrated)
        ch0 = store.data["nikobus_module"]["AABB"]["channels"][0]
        self.assertEqual(ch0["description"], "Counter")
        self.assertEqual(ch0["entity_type"], "light")
        self.assertEqual(ch0["led_on"], "1A2B3C")
        self.assertEqual(ch0["led_off"], "4D5E6F")
        # Roller travel time gets split via convert_legacy_to_flat then
        # overlaid:
        roller_ch = store.data["nikobus_module"]["CCDD"]["channels"][0]
        self.assertEqual(roller_ch["operation_time_up"], "30")
        self.assertEqual(roller_ch["operation_time_down"], "30")
        self.assertFalse(os.path.exists(legacy_path))
        self.assertTrue(os.path.exists(legacy_path + ".migrated"))

    def test_legacy_address_not_in_store_is_skipped_not_added(self):
        legacy_path = self._write_legacy({
            "switch_module": [
                {"description": "Ghost", "address": "9999", "channels": []}
            ]
        })
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "AABB": {
                    "module_type": "switch_module",
                    "description": "Real",
                    "channels": [],
                }
            }
        })

        migrated = _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertTrue(migrated)
        # Ghost address from legacy must NOT land in the Store — discovery
        # is the source of truth for what exists.
        self.assertNotIn("9999", store.data["nikobus_module"])
        self.assertIn("AABB", store.data["nikobus_module"])
        # File still renamed.
        self.assertTrue(os.path.exists(legacy_path + ".migrated"))

    def test_legacy_extra_channels_silently_dropped(self):
        # 05-057 4→2 channel-count correction: legacy says 4 channels,
        # discovery says 2. Overlay applies to indices 0-1 only; legacy
        # entries for indices 2-3 are dropped without complaint.
        self._write_legacy({
            "switch_module": [{
                "description": "05-057 device", "address": "1F2E",
                "channels": [
                    {"description": "Ch1 user"},
                    {"description": "Ch2 user"},
                    {"description": "Ch3 ghost"},
                    {"description": "Ch4 ghost"},
                ],
            }]
        })
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "1F2E": {
                    "module_type": "switch_module",
                    "description": "05-057",
                    "channels": [
                        {"description": "Channel 1"},
                        {"description": "Channel 2"},
                    ],
                }
            }
        })

        migrated = _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertTrue(migrated)
        channels = store.data["nikobus_module"]["1F2E"]["channels"]
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]["description"], "Ch1 user")
        self.assertEqual(channels[1]["description"], "Ch2 user")

    def test_overlay_does_not_clobber_with_empty_legacy_values(self):
        # If the legacy file has empty-string description for a channel,
        # don't blow away whatever discovery set.
        self._write_legacy({
            "switch_module": [{
                "description": "Studio", "address": "5678",
                "channels": [{"description": ""}],
            }]
        })
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "5678": {
                    "module_type": "switch_module",
                    "description": "Compact",
                    "channels": [{"description": "discovered ch"}],
                }
            }
        })

        _run(nkbmigration.async_migrate_legacy_module_config(self.hass, store))

        self.assertEqual(
            store.data["nikobus_module"]["5678"]["channels"][0]["description"],
            "discovered ch",
        )


if __name__ == "__main__":
    unittest.main()
