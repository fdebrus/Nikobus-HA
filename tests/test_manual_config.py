"""Tests for the manual-configuration loader (``nkbmanual``).

Manual-config mode is opt-in via the config flow's ``manual_config``
toggle. When enabled, the integration re-applies the v1 JSON files on
every startup (no rename) so installs whose firmware auto-discovery
cannot crack — typically pre-Gen3 / Gen2 PC-Link, per nikobus-connect
0.5.24 CHANGELOG — can keep their inventory declarative.

These tests pin the load shape, the merge semantics (YAML wins for
inventory fields, options-flow edits survive), the ``.migrated``
fallback for users who already upgraded once, and the single-key
button-entry shape that makes the v2 router route presses correctly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

# Reuse the same conftest hooks the migration tests use.
ROOT = Path(__file__).parent.parent
COMP = ROOT / "custom_components" / "nikobus"


# Sanity: the real aiofiles is patched in by conftest / test_module_migration.
# Importing test_module_migration first installs the real-file stub on the
# module-level aiofiles object that nkbmanual / nkbmigration end up using.
import tests.test_module_migration as _mig_tests  # noqa: F401,E402


def _load(name, path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = ".".join(name.split(".")[:-1])
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Reuse the shared NikobusModuleStorage stub by mirroring its API.
class _InMemoryModuleStore:
    def __init__(self, initial=None):
        self._data = initial if initial is not None else {"nikobus_module": {}}
        self.save_count = 0

    async def async_load(self):
        return self._data

    async def async_save(self):
        self.save_count += 1

    @property
    def data(self):
        return self._data

    @property
    def is_empty(self):
        return not bool(self._data.get("nikobus_module"))


class _FakeConfig:
    def __init__(self, config_dir: str):
        self.config_dir = config_dir

    def path(self, filename: str) -> str:
        return os.path.join(self.config_dir, filename)


class _FakeHass:
    def __init__(self, config_dir: str):
        self.config = _FakeConfig(config_dir)

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


# Load nkbmanual fresh (its imports of nkbmigration / nkbstorage are
# already registered by the module-migration test bootstrap).
nkbmanual = _load(
    "custom_components.nikobus.nkbmanual", COMP / "nkbmanual.py"
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestApplyManualConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = self._tmp.name
        self.hass = _FakeHass(self.config_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, filename: str, payload):
        path = os.path.join(self.config_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    # ------------------------------------------------------------------
    # Smoke: no files = no-op
    # ------------------------------------------------------------------

    def test_no_files_returns_false(self):
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )

        self.assertFalse(changed)
        self.assertEqual(store.data, {"nikobus_module": {}})
        self.assertEqual(button_data, {"nikobus_button": {}})

    # ------------------------------------------------------------------
    # Module file: applied into an empty store
    # ------------------------------------------------------------------

    def test_modules_applied_into_empty_store(self):
        path = self._write("nikobus_module_config.json", {
            "switch_module": [{
                "description": "Living lights",
                "model": "05-000-02",
                "address": "C9A5",
                "channels": [
                    {"description": "Sofa"},
                    {"description": "Pendant"},
                ],
            }],
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )

        self.assertTrue(changed)
        entry = store.data["nikobus_module"]["C9A5"]
        self.assertEqual(entry["module_type"], "switch_module")
        self.assertEqual(entry["description"], "Living lights")
        self.assertEqual(entry["model"], "05-000-02")
        self.assertEqual(entry["channels"][0]["description"], "Sofa")
        # Manual-config marker on discovered_info so diagnostics can
        # tell apart YAML-sourced rows from auto-discovered ones.
        self.assertEqual(entry["discovered_info"]["source"], "manual_config")
        # Source file stays in place — no .migrated rename.
        self.assertTrue(os.path.exists(path))

    # ------------------------------------------------------------------
    # Merge: YAML wins for inventory, options-flow edits survive
    # ------------------------------------------------------------------

    def test_options_flow_channel_edits_survive_reapply(self):
        self._write("nikobus_module_config.json", {
            "switch_module": [{
                "description": "Kitchen",
                "address": "AABB",
                "channels": [
                    {"description": "Counter"},
                    {"description": "Pendant"},
                ],
            }],
        })
        # Simulate a populated store with an options-flow channel edit:
        # the user has set entity_type=light + led_on on channel 1.
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "AABB": {
                    "module_type": "switch_module",
                    "description": "Stale auto-discovery name",
                    "model": "05-000-02",
                    "channels": [
                        {
                            "description": "Counter (will be overwritten)",
                            "entity_type": "light",
                            "led_on": "1A2B3C",
                        },
                        {"description": "Pendant"},
                    ],
                }
            }
        })
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        entry = store.data["nikobus_module"]["AABB"]
        # YAML wins for description (both module and channel level).
        self.assertEqual(entry["description"], "Kitchen")
        self.assertEqual(entry["channels"][0]["description"], "Counter")
        # Options-flow-only fields survive.
        self.assertEqual(entry["channels"][0]["entity_type"], "light")
        self.assertEqual(entry["channels"][0]["led_on"], "1A2B3C")

    # ------------------------------------------------------------------
    # .migrated fallback for users who already upgraded once.
    # ------------------------------------------------------------------

    def test_migrated_fallback(self):
        self._write("nikobus_module_config.json.migrated", {
            "dimmer_module": [{
                "description": "Salon dimmer",
                "address": "0E6C",
                "channels": [{"description": "Sconces"}],
            }],
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )

        self.assertTrue(changed)
        self.assertIn("0E6C", store.data["nikobus_module"])
        self.assertEqual(
            store.data["nikobus_module"]["0E6C"]["module_type"], "dimmer_module"
        )

    # ------------------------------------------------------------------
    # Buttons: list form (v1 canonical) — one entry → one routable record
    # ------------------------------------------------------------------

    def test_button_list_form_creates_single_key_records(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {"address": "112233", "description": "Hallway"},
                {"address": "445566", "description": "Bedroom"},
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )

        self.assertTrue(changed)
        buttons = button_data["nikobus_button"]
        self.assertIn("112233", buttons)
        self.assertIn("445566", buttons)

        hallway = buttons["112233"]
        # Single-key shape — the v2 router routes by op-point bus_address,
        # so one op-point per v1 entry is enough.
        self.assertEqual(hallway["channels"], 1)
        self.assertEqual(list(hallway["operation_points"].keys()), ["1A"])
        self.assertEqual(
            hallway["operation_points"]["1A"]["bus_address"], "112233"
        )
        self.assertEqual(hallway["description"], "Hallway")

    # ------------------------------------------------------------------
    # Buttons: dict form (v1 post-transform shape) also accepted.
    # ------------------------------------------------------------------

    def test_button_dict_form_accepted(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": {
                "AABBCC": {"description": "Kitchen"},
            }
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        self.assertIn("AABBCC", button_data["nikobus_button"])
        self.assertEqual(
            button_data["nikobus_button"]["AABBCC"]
            ["operation_points"]["1A"]["bus_address"],
            "AABBCC",
        )

    # ------------------------------------------------------------------
    # Buttons: re-apply preserves an HA-side renamed description and
    # any auto-discovered op-points are pruned back to "1A".
    # ------------------------------------------------------------------

    def test_button_reapply_preserves_user_rename_and_prunes_strays(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {"address": "112233", "description": "#N112233"},  # placeholder
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {
            "112233": {
                "description": "Hallway (renamed by user)",
                "channels": 4,
                "operation_points": {
                    "1A": {"bus_address": "112233"},
                    # A stray op-point from a previous auto-discovery —
                    # manual mode owns the shape, so this gets pruned.
                    "1B": {"bus_address": "999999"},
                },
            }
        }}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        entry = button_data["nikobus_button"]["112233"]
        # User-set description is preserved (placeholder didn't overwrite it).
        self.assertEqual(entry["description"], "Hallway (renamed by user)")
        # Stray "1B" was pruned — only "1A" remains.
        self.assertEqual(list(entry["operation_points"].keys()), ["1A"])

    # ------------------------------------------------------------------
    # Unreadable file is logged + swallowed; the loader still returns.
    # ------------------------------------------------------------------

    def test_unreadable_file_logged_and_skipped(self):
        # Invalid JSON
        path = os.path.join(self.config_dir, "nikobus_module_config.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not JSON")

        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        # Must not raise.
        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )
        self.assertFalse(changed)
        self.assertEqual(store.data, {"nikobus_module": {}})


if __name__ == "__main__":
    unittest.main()
