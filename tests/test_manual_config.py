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

ROOT = Path(__file__).parent.parent
COMP = ROOT / "custom_components" / "nikobus"


# Replace the conftest aiofiles stub (an AsyncMock that doesn't read
# real files) with a real-file implementation that uses run-in-executor
# wrappers. Needed because nkbmanual reads JSON files via aiofiles.


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
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(
            None, self._fh.read
        )

    async def write(self, data: str) -> int:
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(
            None, self._fh.write, data
        )


def _real_aiofiles_open(path: str, mode: str = "r"):
    return _AsyncFileContext(path, mode)


_aiofiles_stub = sys.modules.get("aiofiles")
if _aiofiles_stub is not None:
    _aiofiles_stub.open = _real_aiofiles_open  # type: ignore[attr-defined]


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
    # 2.11.4: ``.migrated`` fallback removed. Users with leftover
    # ``.migrated`` files from earlier versions must rename them back
    # to the canonical name themselves — the loader only looks at the
    # canonical filename.
    # ------------------------------------------------------------------

    def test_migrated_file_is_not_picked_up(self):
        # File present only under the ``.migrated`` suffix — loader
        # must NOT read it (closed path).
        migrated_path = self._write("nikobus_module_config.json.migrated", {
            "dimmer_module": [{
                "description": "Salon dimmer",
                "address": "0E6C",
                "channels": [{"description": "Sconces"}],
            }],
        })
        canonical_path = os.path.join(
            self.config_dir, "nikobus_module_config.json"
        )
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )

        # Module store stays empty — ``.migrated`` not read.
        self.assertNotIn("0E6C", store.data["nikobus_module"])
        # No automatic rename either — file stays where the user left it.
        self.assertFalse(os.path.exists(canonical_path))
        self.assertTrue(os.path.exists(migrated_path))

    # ------------------------------------------------------------------
    # Buttons: a 4-key physical wall button (4 v1 entries) is grouped
    # into ONE v2 record with four operation_points — preserves the
    # device-registry "one device per physical button" UX.
    # ------------------------------------------------------------------

    def test_button_entries_group_by_physical_address(self):
        # Four v1 entries with the same linked_button.address — one
        # physical 4-key keypad, four bus addresses.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "004E2C",
                    "description": "Sofa wall light up",
                    "linked_button": [{
                        "type": "IR Button with 4 Operation Points",
                        "model": "05-348",
                        "address": "0D1C80",
                        "channels": 4,
                        "key": "1C",
                    }],
                },
                {
                    "address": "404E2C",
                    "description": "Sofa wall light down",
                    "linked_button": [{
                        "type": "IR Button with 4 Operation Points",
                        "model": "05-348",
                        "address": "0D1C80",
                        "channels": 4,
                        "key": "1D",
                    }],
                },
                {
                    "address": "804E2C",
                    "description": "Sofa shutter open",
                    "linked_button": [{
                        "type": "IR Button with 4 Operation Points",
                        "model": "05-348",
                        "address": "0D1C80",
                        "channels": 4,
                        "key": "1A",
                    }],
                },
                {
                    "address": "C04E2C",
                    "description": "Sofa shutter close",
                    "linked_button": [{
                        "type": "IR Button with 4 Operation Points",
                        "model": "05-348",
                        "address": "0D1C80",
                        "channels": 4,
                        "key": "1B",
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        buttons = button_data["nikobus_button"]
        # One physical record, NOT four.
        self.assertEqual(list(buttons.keys()), ["0D1C80"])
        phys = buttons["0D1C80"]
        self.assertEqual(phys["channels"], 4)
        self.assertEqual(phys["model"], "05-348")
        self.assertEqual(phys["type"], "IR Button with 4 Operation Points")
        # All four keys present, each with its own bus_address +
        # description from the v1 entry it came from.
        op_points = phys["operation_points"]
        self.assertEqual(set(op_points.keys()), {"1A", "1B", "1C", "1D"})
        self.assertEqual(op_points["1C"]["bus_address"], "004E2C")
        self.assertEqual(op_points["1C"]["description"], "Sofa wall light up")
        self.assertEqual(op_points["1A"]["bus_address"], "804E2C")

    # ------------------------------------------------------------------
    # Buttons: 8-channel keypads carry keys "2A" through "2D" alongside
    # the "1A"-"1D" set; the loader doesn't constrain key labels.
    # ------------------------------------------------------------------

    def test_eight_channel_keypad(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "01E3EE",
                    "description": "Bedroom Right Light On",
                    "linked_button": [{
                        "type": "Button with 8 Operation Points",
                        "model": "05-349",
                        "address": "1DF1E0",
                        "channels": 8,
                        "key": "2C",
                    }],
                },
                {
                    "address": "41E3EE",
                    "description": "Bedroom Right Light Off",
                    "linked_button": [{
                        "type": "Button with 8 Operation Points",
                        "model": "05-349",
                        "address": "1DF1E0",
                        "channels": 8,
                        "key": "2D",
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        phys = button_data["nikobus_button"]["1DF1E0"]
        self.assertEqual(phys["channels"], 8)
        self.assertEqual(set(phys["operation_points"].keys()), {"2C", "2D"})

    # ------------------------------------------------------------------
    # Buttons: entries without a linked_button block (IR/virtual/scene
    # triggers, auto-generated DISCOVERED placeholders) fall back to a
    # synthetic single-key record so the bus address still routes.
    # ------------------------------------------------------------------

    def test_unlinked_entry_falls_back_to_single_key(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                # IR scene trigger — no linked_button at all
                {"address": "9E4E2C", "description": "Living scene TV lights",
                 "impacted_module": [{"address": "0E6C", "group": "1"}]},
                # Auto-generated placeholder — no linked_button
                {"address": "FFFFFF",
                 "description": "DISCOVERED - Nikobus Button #NFFFFFF",
                 "impacted_module": [{"address": "", "group": ""}]},
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        buttons = button_data["nikobus_button"]
        self.assertIn("9E4E2C", buttons)
        self.assertIn("FFFFFF", buttons)
        ir = buttons["9E4E2C"]
        self.assertEqual(ir["channels"], 1)
        self.assertEqual(ir["operation_points"]["1A"]["bus_address"], "9E4E2C")
        self.assertEqual(ir["description"], "Living scene TV lights")

    # ------------------------------------------------------------------
    # Buttons: dict form (v1-post-transform shape) also accepted.
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
    # Declarative: removing entries from the file removes them on reapply.
    # ------------------------------------------------------------------

    def test_button_removal_propagates_on_reapply(self):
        button_data = {"nikobus_button": {
            "0D1C80": {
                "description": "Old IR button",
                "channels": 4,
                "operation_points": {
                    "1A": {"bus_address": "804E2C"},
                    "1B": {"bus_address": "C04E2C"},
                },
            },
            "1DF1E0": {
                "description": "Bedroom keypad",
                "channels": 8,
                "operation_points": {"2C": {"bus_address": "01E3EE"}},
            },
        }}
        # File only declares the bedroom keypad now.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "01E3EE",
                    "description": "Bedroom Right Light On",
                    "linked_button": [{
                        "type": "Button with 8 Operation Points",
                        "model": "05-349",
                        "address": "1DF1E0",
                        "channels": 8,
                        "key": "2C",
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        buttons = button_data["nikobus_button"]
        self.assertNotIn("0D1C80", buttons)
        self.assertIn("1DF1E0", buttons)

    # ------------------------------------------------------------------
    # linked_modules carry through verbatim onto the matching op-point,
    # including v1-specific extras (mode, t1, t2, payload, ir_*). This
    # matters because a diagnostics dump on a manual-mode install
    # should look like an auto-discovered one — same "what does this
    # button control" cross-reference.
    # ------------------------------------------------------------------

    def test_linked_modules_NOT_imported_on_grouped_op_point(self):
        # 2.11.4: ``linked_modules`` lives on the bus side and is
        # populated by step 2 (per-module register scan), NOT by
        # the manual import. Even when the file carries link data
        # (e.g., a snapshot from a previous discovery), the import
        # must drop it — otherwise step 2's fresh records would
        # collide with stale file data.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "004E2C",
                    "description": "Sofa wall light up",
                    "linked_button": [{
                        "type": "IR Button with 4 Operation Points",
                        "model": "05-348",
                        "address": "0D1C80",
                        "channels": 4,
                        "key": "1C",
                    }],
                    "linked_modules": [{
                        "module_address": "0E6C",
                        "outputs": [{
                            "channel": 1,
                            "mode": "M01 (Dim on/off (2 buttons))",
                            "payload": "FFB4000000007234",
                            "button_address": "0D1C80",
                        }],
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        op_point = button_data["nikobus_button"]["0D1C80"]["operation_points"]["1C"]
        # Op-point structure is created (step-1 inventory) but
        # ``linked_modules`` is empty (step-2 territory).
        self.assertEqual(op_point["bus_address"], "004E2C")
        self.assertEqual(op_point["linked_modules"], [])

    def test_linked_modules_NOT_imported_on_unlinked_entry(self):
        # Same rule for the synthetic single-key fallback (IR /
        # scene / virtual): step 1 establishes the op-point shape,
        # step 2 populates link records.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "9E4E2C",
                    "description": "IR scene trigger",
                    "linked_modules": [{
                        "module_address": "0E6C",
                        "outputs": [{"channel": 1, "mode": "M01 (On / off)"}],
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        op_point = button_data["nikobus_button"]["9E4E2C"]["operation_points"]["1A"]
        self.assertEqual(op_point["linked_modules"], [])

    def test_missing_linked_modules_defaults_to_empty(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "112233",
                    "description": "No links",
                    "linked_button": [{
                        "type": "Button with 4 Operation Points",
                        "model": "05-346",
                        "address": "1CFE4A",
                        "channels": 4,
                        "key": "1A",
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        op_point = button_data["nikobus_button"]["1CFE4A"]["operation_points"]["1A"]
        self.assertEqual(op_point["linked_modules"], [])

    # ------------------------------------------------------------------
    # Regression: an op-point whose bus address collides with another
    # physical's address must not eclipse the proper grouping.
    # ------------------------------------------------------------------

    def test_unlinked_does_not_overwrite_grouped_physical(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                # Grouped first — physical 0D1C80
                {
                    "address": "004E2C",
                    "description": "Real key",
                    "linked_button": [{
                        "type": "IR Button with 4 Operation Points",
                        "model": "05-348",
                        "address": "0D1C80",
                        "channels": 4,
                        "key": "1C",
                    }],
                },
                # Standalone entry whose bus address happens to equal
                # the grouped physical's address — must not overwrite.
                {"address": "0D1C80", "description": "Stray placeholder"},
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        buttons = button_data["nikobus_button"]
        # The grouped physical record wins; the standalone is dropped.
        self.assertEqual(buttons["0D1C80"]["channels"], 4)
        self.assertIn("1C", buttons["0D1C80"]["operation_points"])

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


class TestRealWorldSample(unittest.TestCase):
    """End-to-end pin against the live install sample (issue ##).

    The sample contains every category that matters: switch / dimmer /
    roller modules with mixed channel-level options (entity_type,
    operation_time_up/down), pc_link / pc_logic / feedback_module
    sentinel sections, an empty other_module list, a 4-key IR keypad
    (4 entries), an 8-channel keypad (2C/2D entries), virtual IR
    scene-trigger entries with no linked_button, and DISCOVERED
    placeholders. If the loader handles this file the user does not
    need to edit anything.
    """

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

    def test_real_world_modules_sample(self):
        self._write("nikobus_module_config.json", {
            "switch_module": [
                {
                    "description": "switch_module_s1",
                    "model": "05-000-02",
                    "address": "C9A5",
                    "channels": [
                        {"description": "Extérieur Porte Entrée",
                         "entity_type": "light"},
                        {"description": "Hall RDC Lumière",
                         "entity_type": "light"},
                    ],
                    "discovered_info": {
                        "name": "Switch Module", "device_type": "01",
                        "channels_count": 12,
                    },
                },
            ],
            "roller_module": [{
                "description": "rollershutter_module_r1",
                "model": "05-001-02",
                "address": "9105",
                "channels": [
                    {"description": "Salon Volet Sofa",
                     "operation_time_up": "29",
                     "operation_time_down": "27"},
                    {"description": "not_in_use output_1",
                     "operation_time_up": "30"},
                ],
                "discovered_info": {
                    "name": "Roller Shutter Module", "device_type": "02",
                    "channels_count": 6,
                },
            }],
            "pc_link": [{
                "description": "pc_link_pcl1",
                "model": "05-200",
                "address": "86F5",
                "discovered_info": {
                    "name": "PC Link", "device_type": "0A",
                },
            }],
            "feedback_module": [{
                "description": "feedback_module_fb1",
                "model": "05-207",
                "address": "966C",
                "discovered_info": {
                    "name": "Feedback Module", "device_type": "42",
                },
            }],
            "other_module": [],
        })

        store = _InMemoryModuleStore()
        _run(nkbmanual.async_apply_manual_config(
            self.hass, store, {"nikobus_button": {}}))

        modules = store.data["nikobus_module"]
        self.assertIn("C9A5", modules)
        self.assertIn("9105", modules)
        self.assertIn("86F5", modules)
        self.assertIn("966C", modules)

        # Channel-level options carry through.
        c9a5_ch0 = modules["C9A5"]["channels"][0]
        self.assertEqual(c9a5_ch0["description"], "Extérieur Porte Entrée")
        self.assertEqual(c9a5_ch0["entity_type"], "light")

        roller_ch = modules["9105"]["channels"][0]
        self.assertEqual(roller_ch["operation_time_up"], "29")
        self.assertEqual(roller_ch["operation_time_down"], "27")

        # discovered_info from v1 file survives, manual_config source
        # marker added alongside the existing keys.
        di = modules["C9A5"]["discovered_info"]
        self.assertEqual(di["device_type"], "01")
        self.assertEqual(di["channels_count"], 12)
        self.assertEqual(di["source"], "manual_config")

        # System modules (pc_link / feedback) keep their module_type
        # bucket and channels=[] is fine for non-output modules.
        self.assertEqual(modules["86F5"]["module_type"], "pc_link")
        self.assertEqual(modules["966C"]["module_type"], "feedback_module")

    def test_real_world_buttons_sample(self):
        # Two 4-key wall buttons sharing physicals 0D1C80 and 1CA840,
        # plus an 8-key keypad 1DF1E0 with two of its 2X keys, plus
        # an IR scene trigger without linked_button, plus a discovered
        # placeholder.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                # 0D1C80 — 4 entries
                {"address": "004E2C", "description": "BT_Sofa_Wall_Up",
                 "linked_button": [{"type": "IR Button with 4 Operation Points",
                                    "model": "05-348", "address": "0D1C80",
                                    "channels": 4, "key": "1C"}]},
                {"address": "404E2C", "description": "BT_Sofa_Wall_Down",
                 "linked_button": [{"type": "IR Button with 4 Operation Points",
                                    "model": "05-348", "address": "0D1C80",
                                    "channels": 4, "key": "1D"}]},
                {"address": "804E2C", "description": "BT_Sofa_Shutter_Open",
                 "linked_button": [{"type": "IR Button with 4 Operation Points",
                                    "model": "05-348", "address": "0D1C80",
                                    "channels": 4, "key": "1A"}]},
                {"address": "C04E2C", "description": "BT_Sofa_Shutter_Close",
                 "linked_button": [{"type": "IR Button with 4 Operation Points",
                                    "model": "05-348", "address": "0D1C80",
                                    "channels": 4, "key": "1B"}]},
                # 1CA840 — 2 entries (only 1A + 1B used in install)
                {"address": "00854E", "description": "BT_Parking_On",
                 "linked_button": [{"type": "Button with 4 Operation Points",
                                    "model": "05-346", "address": "1CA840",
                                    "channels": 4, "key": "1C"}]},
                {"address": "40854E", "description": "BT_Parking_Off",
                 "linked_button": [{"type": "Button with 4 Operation Points",
                                    "model": "05-346", "address": "1CA840",
                                    "channels": 4, "key": "1D"}]},
                # 1DF1E0 — 8-channel keypad
                {"address": "01E3EE", "description": "BT_Right_On",
                 "linked_button": [{"type": "Button with 8 Operation Points",
                                    "model": "05-349", "address": "1DF1E0",
                                    "channels": 8, "key": "2C"}]},
                {"address": "41E3EE", "description": "BT_Right_Off",
                 "linked_button": [{"type": "Button with 8 Operation Points",
                                    "model": "05-349", "address": "1DF1E0",
                                    "channels": 8, "key": "2D"}]},
                # IR scene trigger — no linked_button
                {"address": "9E4E2C", "description": "IR_TV_Lights",
                 "impacted_module": [{"address": "0E6C", "group": "1"}]},
                # Auto-generated placeholder — no linked_button
                {"address": "FFFFFF",
                 "description": "DISCOVERED - Nikobus Button #NFFFFFF",
                 "impacted_module": [{"address": "", "group": ""}]},
            ]
        })

        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}
        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        buttons = button_data["nikobus_button"]
        # 3 physical buttons + 2 standalone = 5 v2 records out of 10
        # v1 entries.
        self.assertEqual(len(buttons), 5)
        self.assertEqual(buttons["0D1C80"]["channels"], 4)
        self.assertEqual(set(buttons["0D1C80"]["operation_points"].keys()),
                         {"1A", "1B", "1C", "1D"})
        # Bus-address routing for the keys.
        self.assertEqual(
            buttons["0D1C80"]["operation_points"]["1C"]["bus_address"], "004E2C")
        self.assertEqual(buttons["1DF1E0"]["channels"], 8)
        self.assertEqual(set(buttons["1DF1E0"]["operation_points"].keys()),
                         {"2C", "2D"})
        # Standalone fallback.
        self.assertEqual(buttons["9E4E2C"]["channels"], 1)
        self.assertEqual(buttons["9E4E2C"]["description"], "IR_TV_Lights")
        self.assertIn("FFFFFF", buttons)

    # ------------------------------------------------------------------
    # 2.11.4: explicit pin for the "linked_modules empty on import" rule.
    # Every op-point produced by the importer must have an empty list,
    # regardless of whether the file declared link data or not.
    # ------------------------------------------------------------------

    def test_all_op_points_have_empty_linked_modules_after_import(self):
        self._write("nikobus_button_config.json", {
            "nikobus_button": [
                {
                    "address": "004E2C",
                    "linked_button": [{
                        "type": "Bus push button, 4 control buttons",
                        "model": "05-064", "address": "0D1C80",
                        "channels": 4, "key": "1C",
                    }],
                    # File carries stale step-2 data: must be dropped.
                    "linked_modules": [{
                        "module_address": "0E6C",
                        "outputs": [{"channel": 1, "mode": "M01"}],
                    }],
                },
                {
                    "address": "404E2C",
                    "linked_button": [{
                        "type": "Bus push button, 4 control buttons",
                        "model": "05-064", "address": "0D1C80",
                        "channels": 4, "key": "1D",
                    }],
                    # No link data in this entry — also empty after import.
                },
                {
                    # Synthetic (no linked_button): single-key fallback.
                    "address": "9E4E2C",
                    "description": "IR_TV_Lights",
                    "linked_modules": [{
                        "module_address": "0E6C",
                        "outputs": [{"channel": 2, "mode": "M01"}],
                    }],
                },
            ]
        })
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        _run(nkbmanual.async_apply_manual_config(self.hass, store, button_data))

        # Walk every op-point on every physical and verify empty.
        for phys_addr, phys in button_data["nikobus_button"].items():
            for key, op in phys["operation_points"].items():
                self.assertEqual(
                    op["linked_modules"], [],
                    f"{phys_addr}:{key} must have empty linked_modules "
                    f"after step-1 import; got {op['linked_modules']!r}",
                )


class TestApplyFriendlyNameOverlay(unittest.TestCase):
    """2.11.5: the overlay function enriches existing store entries
    with user-editable fields from the manual files, without adding
    or removing entries. Used by the unified step-1 discovery whether
    the inventory source was PC-Link or the file itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_dir = self.tmp.name
        self.hass = _FakeHass(self.config_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, filename: str, data: dict) -> str:
        path = os.path.join(self.config_dir, filename)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_overlay_copies_module_and_channel_descriptions(self):
        # Simulate PC-Link inventory result: address known, generic name.
        store = _InMemoryModuleStore()
        store.data["nikobus_module"] = {
            "C9A5": {
                "module_type": "switch_module",
                "description": "Switch Module",  # PC-Link's default
                "model": "05-000-02",
                "channels": [
                    {"description": ""},  # Generic
                    {"description": ""},
                ],
            }
        }
        button_data = {"nikobus_button": {}}

        # Manual file with user-edited descriptions.
        self._write("nikobus_module_config.json", {
            "switch_module": [{
                "description": "switch_module_s1",
                "address": "C9A5",
                "model": "05-000-02",
                "channels": [
                    {"description": "Extérieur Porte Entrée", "entity_type": "light"},
                    {"description": "Hall 1er Étage", "entity_type": "light"},
                ],
            }]
        })

        changed = _run(
            nkbmanual.async_apply_friendly_name_overlay(
                self.hass, store, button_data
            )
        )

        self.assertTrue(changed)
        c9a5 = store.data["nikobus_module"]["C9A5"]
        self.assertEqual(c9a5["description"], "switch_module_s1")
        self.assertEqual(c9a5["channels"][0]["description"], "Extérieur Porte Entrée")
        self.assertEqual(c9a5["channels"][0]["entity_type"], "light")
        self.assertEqual(c9a5["channels"][1]["description"], "Hall 1er Étage")

    def test_overlay_does_not_add_modules_not_in_store(self):
        # PC-Link discovered C9A5; manual file declares EXTRA module BEEF.
        # Overlay must ignore BEEF — PC-Link is the inventory authority.
        store = _InMemoryModuleStore()
        store.data["nikobus_module"] = {
            "C9A5": {
                "module_type": "switch_module",
                "description": "Switch Module",
                "channels": [],
            }
        }
        button_data = {"nikobus_button": {}}

        self._write("nikobus_module_config.json", {
            "switch_module": [
                {"description": "s1", "address": "C9A5", "channels": []},
                {"description": "Phantom", "address": "BEEF", "channels": []},
            ]
        })

        _run(
            nkbmanual.async_apply_friendly_name_overlay(
                self.hass, store, button_data
            )
        )

        modules = store.data["nikobus_module"]
        self.assertIn("C9A5", modules)
        self.assertNotIn("BEEF", modules)
        self.assertEqual(modules["C9A5"]["description"], "s1")

    def test_overlay_preserves_user_fields_for_rollers(self):
        store = _InMemoryModuleStore()
        store.data["nikobus_module"] = {
            "9105": {
                "module_type": "roller_module",
                "description": "Roller Shutter Module",
                "channels": [
                    {"description": ""},
                    {"description": ""},
                ],
            }
        }
        button_data = {"nikobus_button": {}}

        self._write("nikobus_module_config.json", {
            "roller_module": [{
                "description": "rollershutter_module_r1",
                "address": "9105",
                "channels": [
                    {"description": "Salon Volet Terrasse", "operation_time_up": "45"},
                    {"description": "Salon Volet Jardin", "operation_time_up": "51"},
                ],
            }]
        })

        _run(
            nkbmanual.async_apply_friendly_name_overlay(
                self.hass, store, button_data
            )
        )

        chans = store.data["nikobus_module"]["9105"]["channels"]
        self.assertEqual(chans[0]["description"], "Salon Volet Terrasse")
        self.assertEqual(chans[0]["operation_time_up"], "45")
        self.assertEqual(chans[1]["operation_time_up"], "51")

    def test_overlay_updates_button_op_point_descriptions(self):
        # Existing physical button in store (from PC-Link discovery).
        store = _InMemoryModuleStore()
        button_data = {
            "nikobus_button": {
                "0D1C80": {
                    "type": "IR Button with 4 Operation Points",
                    "model": "05-348",
                    "channels": 4,
                    "description": "IR Button",
                    "operation_points": {
                        "1C": {
                            "bus_address": "004E2C",
                            "description": "Push button 1C #N004E2C",
                            "linked_modules": [],
                        }
                    },
                }
            }
        }

        # File supplies a friendlier name for OP 1C.
        self._write("nikobus_button_config.json", {
            "nikobus_button": [{
                "address": "004E2C",
                "description": "BT_GF_Living_Sofa_Wall_Light_Up",
                "linked_button": [{
                    "type": "IR Button with 4 Operation Points",
                    "model": "05-348",
                    "address": "0D1C80",
                    "channels": 4,
                    "key": "1C",
                }],
            }]
        })

        changed = _run(
            nkbmanual.async_apply_friendly_name_overlay(
                self.hass, store, button_data
            )
        )

        self.assertTrue(changed)
        op = button_data["nikobus_button"]["0D1C80"]["operation_points"]["1C"]
        self.assertEqual(op["description"], "BT_GF_Living_Sofa_Wall_Light_Up")

    def test_overlay_noop_when_no_files(self):
        store = _InMemoryModuleStore()
        store.data["nikobus_module"] = {
            "C9A5": {"module_type": "switch_module", "description": "untouched"}
        }
        button_data = {"nikobus_button": {}}

        changed = _run(
            nkbmanual.async_apply_friendly_name_overlay(
                self.hass, store, button_data
            )
        )

        self.assertFalse(changed)
        # Store unchanged.
        self.assertEqual(
            store.data["nikobus_module"]["C9A5"]["description"], "untouched"
        )


class TestConsolidateLegacy1AOnlyButtons(unittest.TestCase):
    """2.11.8: no-PC-Link installs whose manual button-config lists each
    wall-button key face as a separate ``channels=1`` / 1A-only entry
    get those siblings consolidated into the canonical multi-key form
    that PC-Link inventory would have produced.

    Math under test (mirror of nikobus_connect KEY_MAPPING):

      4-key wall button: first nibble's top 2 bits encode the key face
        0x8 → 1A,  0xC → 1B,  0x0 → 1C,  0x4 → 1D
      8-key: top 3 bits encode the face
        0xA → 1A, 0xE → 1B, 0x2 → 1C, 0x6 → 1D,
        0x8 → 2A, 0xC → 2B, 0x0 → 2C, 0x4 → 2D
    """

    def _candidate(self, bus_addr: str, description: str) -> dict:
        return {
            "type": "Manual button",
            "model": "",
            "channels": 1,
            "description": description,
            "operation_points": {
                "1A": {
                    "bus_address": bus_addr,
                    "description": description,
                    "linked_modules": [],
                }
            },
        }

    def test_buro_deur_real_user_data_consolidates_to_one_4key_button(self):
        """The user's actual Buro_deur wall plate: 4 siblings sharing
        physical_id 11C5CE, top 2 bits 10/11/00/01 → 1A/1B/1C/1D."""
        raw = {
            "91C5CE": self._candidate("91C5CE", "Buro_deur_B1"),
            "D1C5CE": self._candidate("D1C5CE", "Buro_deur_O1"),
            "11C5CE": self._candidate("11C5CE", "Buro_deur_B2"),
            "51C5CE": self._candidate("51C5CE", "Buro_deur_O2"),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        # All 4 face entries replaced by one canonical entry at physical_id.
        self.assertEqual(list(result.keys()), ["11C5CE"])
        entry = result["11C5CE"]
        self.assertEqual(entry["channels"], 4)
        self.assertEqual(entry["description"], "Buro_deur")  # common prefix
        self.assertEqual(
            set(entry["operation_points"].keys()), {"1A", "1B", "1C", "1D"}
        )
        # Bus addresses preserved under correct labels.
        self.assertEqual(entry["operation_points"]["1A"]["bus_address"], "91C5CE")
        self.assertEqual(entry["operation_points"]["1B"]["bus_address"], "D1C5CE")
        self.assertEqual(entry["operation_points"]["1C"]["bus_address"], "11C5CE")
        self.assertEqual(entry["operation_points"]["1D"]["bus_address"], "51C5CE")
        # Descriptions preserved per face.
        self.assertEqual(
            entry["operation_points"]["1A"]["description"], "Buro_deur_B1"
        )
        # linked_modules always empty after step 1.
        self.assertEqual(entry["operation_points"]["1A"]["linked_modules"], [])
        # Provenance tag distinguishes from manual_config (un-consolidated).
        self.assertEqual(
            entry["discovered_info"]["source"], "manual_config_consolidated"
        )

    def test_living_buro_8_addresses_split_into_two_4key_buttons(self):
        """Living_buro has 8 buttons that share trailing hex 9E0CE but
        differ in the bottom 2 bits of the first nibble — that's TWO
        physical 4-key buttons, not one 8-key. Bottom bits 01 → 19E0CE,
        bottom bits 11 → 39E0CE."""
        raw = {
            # Group 1: physical_id 19E0CE (first-nibble bottom bits = 01)
            "99E0CE": self._candidate("99E0CE", "Living_buro_B3"),
            "D9E0CE": self._candidate("D9E0CE", "Living_buro_O3"),
            "19E0CE": self._candidate("19E0CE", "Living_buro_B4"),
            "59E0CE": self._candidate("59E0CE", "Living_buro_O4"),
            # Group 2: physical_id 39E0CE (first-nibble bottom bits = 11)
            "B9E0CE": self._candidate("B9E0CE", "Living_buro_B1"),
            "F9E0CE": self._candidate("F9E0CE", "Living_buro_O1"),
            "39E0CE": self._candidate("39E0CE", "Living_buro_B2"),
            "79E0CE": self._candidate("79E0CE", "Living_buro_O2"),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        self.assertEqual(set(result.keys()), {"19E0CE", "39E0CE"})
        for pid in ("19E0CE", "39E0CE"):
            self.assertEqual(result[pid]["channels"], 4)
            self.assertEqual(
                set(result[pid]["operation_points"].keys()),
                {"1A", "1B", "1C", "1D"},
            )

    def test_partial_3_of_4_group_is_NOT_consolidated(self):
        """Best-effort: if the user only listed 3 of 4 faces, leave
        them as singletons rather than mis-grouping as 2-key + orphan."""
        raw = {
            "91C5CE": self._candidate("91C5CE", "Half_B1"),
            "D1C5CE": self._candidate("D1C5CE", "Half_O1"),
            "11C5CE": self._candidate("11C5CE", "Half_B2"),
            # 51C5CE (1D) intentionally absent
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        # Nothing matched a clean 1/2/4/8-key group → all kept singleton.
        self.assertEqual(set(result.keys()), {"91C5CE", "D1C5CE", "11C5CE"})
        for entry in result.values():
            self.assertEqual(entry["channels"], 1)

    def test_two_key_button_consolidates(self):
        """2-key wall button: two siblings with top 2 bits 10 and 11."""
        raw = {
            "8A5500": self._candidate("8A5500", "Toggle_on"),
            "CA5500": self._candidate("CA5500", "Toggle_off"),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        self.assertEqual(list(result.keys()), ["0A5500"])
        entry = result["0A5500"]
        self.assertEqual(entry["channels"], 2)
        self.assertEqual(set(entry["operation_points"].keys()), {"1A", "1B"})
        self.assertEqual(entry["operation_points"]["1A"]["bus_address"], "8A5500")
        self.assertEqual(entry["operation_points"]["1B"]["bus_address"], "CA5500")

    def test_eight_key_button_consolidates(self):
        """8-key wall button: 8 siblings with all even high nibbles
        sharing the same lower 21 bits."""
        # physical_id = 0x000123 (21 bits). All members have bit 0 of
        # first nibble = 0, and first nibble's top 3 bits cover all 8
        # values from KEY_MAPPING[8].
        raw = {
            "A00123": self._candidate("A00123", "Kitchen_1A"),
            "E00123": self._candidate("E00123", "Kitchen_1B"),
            "200123": self._candidate("200123", "Kitchen_1C"),
            "600123": self._candidate("600123", "Kitchen_1D"),
            "800123": self._candidate("800123", "Kitchen_2A"),
            "C00123": self._candidate("C00123", "Kitchen_2B"),
            "000123": self._candidate("000123", "Kitchen_2C"),
            "400123": self._candidate("400123", "Kitchen_2D"),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        # Single consolidated entry at the 21-bit physical_id.
        self.assertEqual(list(result.keys()), ["000123"])
        entry = result["000123"]
        self.assertEqual(entry["channels"], 8)
        self.assertEqual(
            set(entry["operation_points"].keys()),
            {"1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"},
        )

    def test_singleton_orphan_passes_through(self):
        """A lone 1A-only entry with no siblings stays as it is —
        consolidating a 1-key would change nothing meaningful and would
        mask runtime-auto-add provenance."""
        raw = {"E39EF7": self._candidate("E39EF7", "Lone runtime button")}

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        self.assertEqual(result, raw)

    def test_already_multi_key_entry_is_untouched(self):
        """An entry authored in canonical form (channels>1) is left
        completely alone — explicit grouping wins over inference."""
        raw = {
            "11C5CE": {
                "type": "Push button",
                "model": "05-064",
                "channels": 4,
                "description": "Already grouped",
                "operation_points": {
                    "1A": {"bus_address": "91C5CE", "description": "A",
                           "linked_modules": []},
                    "1B": {"bus_address": "D1C5CE", "description": "B",
                           "linked_modules": []},
                    "1C": {"bus_address": "11C5CE", "description": "C",
                           "linked_modules": []},
                    "1D": {"bus_address": "51C5CE", "description": "D",
                           "linked_modules": []},
                },
            },
            # A stray 1A-only sibling that would *naïvely* group at
            # 11C5CE — must NOT clobber the authored entry above.
            "BEEF00": self._candidate("BEEF00", "Unrelated stray"),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        self.assertEqual(result["11C5CE"]["description"], "Already grouped")
        self.assertEqual(result["11C5CE"]["channels"], 4)
        # Stray stays singleton.
        self.assertEqual(result["BEEF00"]["channels"], 1)

    def test_collision_with_existing_authored_entry_skips_consolidation(self):
        """If 4 candidate siblings would consolidate to a physical_id
        that ALREADY has a non-candidate entry, leave the candidates
        alone rather than overwrite authored data."""
        raw = {
            "11C5CE": {
                "type": "Authored",
                "model": "",
                "channels": 1,
                "description": "Not a candidate (extra field)",
                "operation_points": {
                    "1A": {"bus_address": "11C5CE", "description": "A",
                           "linked_modules": []},
                    "scene_1": {"bus_address": "11C5CE", "description": "scene",
                                "linked_modules": []},
                },
            },
            "91C5CE": self._candidate("91C5CE", "would_be_1A"),
            "D1C5CE": self._candidate("D1C5CE", "would_be_1B"),
            "51C5CE": self._candidate("51C5CE", "would_be_1D"),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        # 11C5CE stays as authored (multi op-point on a 1-channel entry).
        self.assertIs(result["11C5CE"], raw["11C5CE"])
        # The 3 candidate siblings can't form a clean 4-key group (only
        # 3 of 4 offsets present) so they stay singleton anyway.
        for addr in ("91C5CE", "D1C5CE", "51C5CE"):
            self.assertEqual(result[addr]["channels"], 1)

    def test_runtime_discovered_singletons_are_kept_untouched(self):
        """The `DISCOVERED -` runtime-auto-add entries with no siblings
        in the manual file (e.g. bus-noise FF-prefix addresses) stay as
        singletons — we don't have enough info to safely group them."""
        raw = {
            "FFFB82": self._candidate(
                "FFFB82", "DISCOVERED - Nikobus Button #NFFFB82"
            ),
            "050000": self._candidate(
                "050000", "DISCOVERED - Nikobus Button #N050000"
            ),
        }

        result = nkbmanual._consolidate_legacy_1a_only_buttons(raw)

        self.assertEqual(set(result.keys()), {"FFFB82", "050000"})
        for entry in result.values():
            self.assertEqual(entry["channels"], 1)

    def test_empty_input_returns_empty(self):
        self.assertEqual(nkbmanual._consolidate_legacy_1a_only_buttons({}), {})


class TestConsolidationEndToEnd(unittest.TestCase):
    """End-to-end: apply_manual_config with the user's actual file shape
    consolidates 1A-only entries into multi-key buttons."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_dir = self.tmp.name
        self.hass = _FakeHass(self.config_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, filename: str, data: dict) -> str:
        path = os.path.join(self.config_dir, filename)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_user_reported_file_shape_consolidates(self):
        """Replays the user's manual button file shape end-to-end through
        async_apply_manual_config. The v1 input format is a list of
        ``{address, description}`` entries with no ``linked_button``
        block — every entry takes the ``_single_key_fallback`` branch,
        producing 1A-only fallback entries that consolidation merges."""
        self._write(
            "nikobus_button_config.json",
            {
                "nikobus_button": [
                    {"address": "91C5CE", "description": "Buro_deur_B1"},
                    {"address": "D1C5CE", "description": "Buro_deur_O1"},
                    {"address": "11C5CE", "description": "Buro_deur_B2"},
                    {"address": "51C5CE", "description": "Buro_deur_O2"},
                ]
            }
        )
        store = _InMemoryModuleStore()
        button_data = {"nikobus_button": {}}

        changed = _run(
            nkbmanual.async_apply_manual_config(self.hass, store, button_data)
        )

        self.assertTrue(changed)
        # 4 face entries collapsed to one canonical multi-key entry.
        self.assertEqual(list(button_data["nikobus_button"].keys()), ["11C5CE"])
        entry = button_data["nikobus_button"]["11C5CE"]
        self.assertEqual(entry["channels"], 4)
        self.assertEqual(
            set(entry["operation_points"].keys()), {"1A", "1B", "1C", "1D"}
        )


if __name__ == "__main__":
    unittest.main()
