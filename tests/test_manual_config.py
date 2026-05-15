"""Tests for the manual-configuration loader (``nkbmanual``).

Manual-config mode is opt-in via the config flow's ``manual_config``
toggle. When enabled, the integration treats the v1 JSON files as the
declarative source of truth: ``nikobus_module_config.json`` and
``nikobus_button_config.json`` are re-applied wholesale on every
reload, so additions, edits, and removals show up after the next
reload.

These tests pin: full replacement (no merge), the ``.migrated``
fallback for users who already upgraded once, the single-key
button-entry shape that makes the v2 router fire HA events on
presses, and the no-files / unreadable-file paths.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Reuse the same conftest hooks the migration tests use.
ROOT = Path(__file__).parent.parent
COMP = ROOT / "custom_components" / "nikobus"


# Importing test_module_migration first installs the real-file aiofiles
# stub that nkbmanual / nkbmigration both end up using.
import tests.test_module_migration as _mig_tests  # noqa: F401,E402


def _load(name, path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = ".".join(name.split(".")[:-1])
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


# Force a fresh load so the test picks up the latest module source.
sys.modules.pop("custom_components.nikobus.nkbmanual", None)
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
    # Smoke: no files = no-op, store untouched.
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
    # Modules: applied into an empty store and tagged with source.
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
        self.assertEqual(entry["discovered_info"]["source"], "manual_config")
        # Source file stays in place — no .migrated rename.
        self.assertTrue(os.path.exists(path))

    # ------------------------------------------------------------------
    # Declarative semantics: removals from the file remove from the store.
    # ------------------------------------------------------------------

    def test_module_removal_propagates_on_reapply(self):
        # Store starts populated with TWO modules.
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "AABB": {"module_type": "switch_module", "description": "stale",
                         "channels": []},
                "CCDD": {"module_type": "dimmer_module", "description": "still here",
                         "channels": []},
            }
        })
        # File now only declares one of them.
        self._write("nikobus_module_config.json", {
            "dimmer_module": [{
                "description": "still here",
                "address": "CCDD",
                "channels": [],
            }],
        })
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        modules = store.data["nikobus_module"]
        self.assertNotIn("AABB", modules)
        self.assertIn("CCDD", modules)

    # ------------------------------------------------------------------
    # Declarative semantics: YAML wins, options-flow edits do not survive.
    # ------------------------------------------------------------------

    def test_file_wins_over_options_flow_state(self):
        # Pretend the user previously customized channel 1 via the
        # options flow — in manual mode that state is NOT preserved
        # across a reload; the file is authoritative.
        store = _InMemoryModuleStore({
            "nikobus_module": {
                "AABB": {
                    "module_type": "switch_module",
                    "description": "stale name",
                    "model": "05-000-02",
                    "channels": [
                        {"description": "Counter",
                         "entity_type": "light",
                         "led_on": "1A2B3C"},
                    ],
                }
            }
        })
        self._write("nikobus_module_config.json", {
            "switch_module": [{
                "description": "Kitchen",
                "address": "AABB",
                "channels": [
                    {"description": "Counter"},  # no entity_type / led_on
                ],
            }],
        })
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        entry = store.data["nikobus_module"]["AABB"]
        self.assertEqual(entry["description"], "Kitchen")
        # Options-flow-only fields are wiped — file is the truth.
        self.assertNotIn("entity_type", entry["channels"][0])
        self.assertNotIn("led_on", entry["channels"][0])

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

    # ------------------------------------------------------------------
    # Buttons: list form (v1 canonical) — one entry → one routable record.
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
    # Buttons: dict form also accepted.
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
    # Declarative semantics: removing a button from the file removes it
    # from the store on the next apply.
    # ------------------------------------------------------------------

    def test_button_removal_propagates_on_reapply(self):
        button_data = {"nikobus_button": {
            "112233": {
                "description": "Old hallway",
                "channels": 1,
                "operation_points": {"1A": {"bus_address": "112233"}},
            },
            "445566": {
                "description": "Bedroom",
                "channels": 1,
                "operation_points": {"1A": {"bus_address": "445566"}},
            },
        }}
        # File only mentions the bedroom now.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {"address": "445566", "description": "Bedroom"},
            ]
        })
        store = _InMemoryModuleStore()

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        buttons = button_data["nikobus_button"]
        self.assertNotIn("112233", buttons)
        self.assertIn("445566", buttons)

    # ------------------------------------------------------------------
    # Unreadable file is logged + swallowed; the loader still returns.
    # ------------------------------------------------------------------

    def test_unreadable_file_logged_and_skipped(self):
        path = os.path.join(self.config_dir, "nikobus_module_config.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not JSON")
        store = _InMemoryModuleStore({"nikobus_module": {
            "AABB": {"module_type": "switch_module", "channels": []},
        }})
        button_data = {"nikobus_button": {}}

        # Must not raise. The unreadable module file should leave the
        # store intact (better than wiping it on a typo).
        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )
        self.assertFalse(changed)
        self.assertIn("AABB", store.data["nikobus_module"])


if __name__ == "__main__":
    unittest.main()
