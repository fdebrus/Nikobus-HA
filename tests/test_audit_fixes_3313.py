"""Fixes from the 2026-10-31 audit and the Feedback Module LED findings (3.31.3)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState

import custom_components.nikobus.binary_sensor as bs
from custom_components.nikobus import (
    _warn_led_keys_driving_other_outputs,
    async_setup_entry,
    nkbmanual,
)
from custom_components.nikobus.config_flow import NikobusConfigFlow, NikobusOptionsFlow
from custom_components.nikobus.const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
)
from custom_components.nikobus.coordinator import NikobusDataCoordinator

_TEST_CONN = "custom_components.nikobus.config_flow._test_connection"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- manual files copied from .storage ---------------------------------------


class _InMemoryModuleStore:
    def __init__(self, initial):
        self._data = initial

    async def async_load(self):
        return self._data

    async def async_save(self):
        pass

    @property
    def data(self):
        return self._data

    @property
    def is_empty(self):
        return not bool(self._data.get("nikobus_module"))


class _FakeHass:
    def __init__(self, config_dir: str):
        self.config = SimpleNamespace(path=lambda name: os.path.join(config_dir, name))

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class _AsyncFile:
    """``aiofiles.open`` on a real file (the conftest stub is a mock)."""

    def __init__(self, path: str, mode: str = "r"):
        self._fh = open(path, mode, encoding="utf-8")  # noqa: SIM115 - closed in __aexit__

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._fh.close()

    async def read(self) -> str:
        return self._fh.read()


SWITCH_MODULE = {
    "module_type": "switch_module",
    "description": "Kitchen",
    "model": "05-000-02",
    "channels": [{"description": "Counter"}],
}


class TestManualFilesCopiedFromTheStore(unittest.TestCase):
    """A file copied straight from ``.storage`` used to replace the store
    with one bogus module, or with no button at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.hass = _FakeHass(self._tmp.name)
        self.store = _InMemoryModuleStore({"nikobus_module": {"AABB": dict(SWITCH_MODULE)}})
        self.buttons = {"nikobus_button": {"1809FE": {"description": "old", "operation_points": {}}}}
        patcher = patch.object(nkbmanual, "aio_open", _AsyncFile)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, filename: str, payload) -> None:
        with open(os.path.join(self._tmp.name, filename), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_module_store_copy_is_read_through_its_envelope(self):
        self._write("nikobus_module_config.json", {
            "version": 1,
            "key": "nikobus.modules",
            "data": {"nikobus_module": {"C9A5": dict(SWITCH_MODULE, description="Hall")}},
        })
        _run(nkbmanual.async_apply_manual_config(self.hass, self.store, self.buttons))
        self.assertEqual(set(self.store.data["nikobus_module"]), {"C9A5"})
        self.assertEqual(self.store.data["nikobus_module"]["C9A5"]["description"], "Hall")

    def test_module_file_without_one_known_type_leaves_the_store_alone(self):
        self._write("nikobus_module_config.json", {"things": [{"address": "C9A5", "channels": []}]})
        with self.assertLogs("custom_components.nikobus.nkbmanual", level=logging.WARNING) as logs:
            _run(nkbmanual.async_apply_manual_config(self.hass, self.store, self.buttons))
        self.assertEqual(set(self.store.data["nikobus_module"]), {"AABB"})
        self.assertTrue(any("left unchanged" in line for line in logs.output))

    def test_empty_button_list_leaves_the_button_store_alone(self):
        self._write("nikobus_button_config.json", {"nikobus_button": []})
        with self.assertLogs("custom_components.nikobus.nkbmanual", level=logging.WARNING) as logs:
            _run(nkbmanual.async_apply_manual_config(self.hass, self.store, self.buttons))
        self.assertEqual(set(self.buttons["nikobus_button"]), {"1809FE"})
        self.assertTrue(any("button store is left unchanged" in line for line in logs.output))

    def test_button_store_copy_keeps_every_key(self):
        plate = {
            "description": "Living 4 keys",
            "model": "05-064",
            "channels": 4,
            "operation_points": {
                "1A": {"bus_address": "8E0383", "linked_modules": []},
                "1B": {"bus_address": "CE0383", "linked_modules": []},
            },
        }
        self._write("nikobus_button_config.json", {
            "version": 1,
            "key": "nikobus.buttons",
            "data": {"nikobus_button": {"0d1c80": plate}},
        })
        _run(nkbmanual.async_apply_manual_config(self.hass, self.store, self.buttons))
        self.assertEqual(set(self.buttons["nikobus_button"]), {"0D1C80"})
        self.assertEqual(
            set(self.buttons["nikobus_button"]["0D1C80"]["operation_points"]), {"1A", "1B"}
        )


# --- a failed setup unloads the platforms it set up ----------------------------


class TestSetupFailureUnloadsThePlatforms(unittest.TestCase):
    """Home Assistant leaves forwarded platforms loaded when setup raises;
    every retry then failed with "has already been setup"."""

    def _setup(self, failing_step: str) -> MagicMock:
        coord = MagicMock()
        coord.connect = AsyncMock()
        coord.stop = AsyncMock()
        coord.async_config_entry_first_refresh = AsyncMock()
        coord.led_key_conflicts.return_value = []
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        restore = AsyncMock()
        if failing_step == "platforms":
            hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError("platform boom")
        else:
            restore.side_effect = RuntimeError("restore boom")
        with (
            patch("custom_components.nikobus.NikobusDataCoordinator", return_value=coord),
            patch("custom_components.nikobus._register_hub_device"),
            patch("custom_components.nikobus._async_restore_button_customisations", restore),
            patch("custom_components.nikobus._async_cleanup_orphan_entities", AsyncMock()),
            patch("custom_components.nikobus.async_call_later"),
            patch("custom_components.nikobus.async_track_time_interval"),
            self.assertRaises(RuntimeError),
        ):
            _run(async_setup_entry(hass, MagicMock()))
        coord.stop.assert_awaited_once()
        return hass

    def test_a_failure_after_the_platforms_unloads_them(self):
        hass = self._setup("restore")
        hass.config_entries.async_unload_platforms.assert_awaited_once()

    def test_a_failure_in_the_platforms_does_not_unload_them(self):
        hass = self._setup("platforms")
        hass.config_entries.async_unload_platforms.assert_not_awaited()


# --- reconfigure --------------------------------------------------------------


def _form(connection: str) -> dict:
    return {
        CONF_CONNECTION_STRING: connection,
        CONF_HAS_FEEDBACK_MODULE: False,
        CONF_PRIOR_GEN3: False,
        CONF_REFRESH_INTERVAL: 120,
    }


def _reconfigure_flow(state, listeners):
    flow = NikobusConfigFlow()
    entry = MagicMock()
    entry.entry_id = "E1"
    entry.state = state
    entry.data = {CONF_CONNECTION_STRING: "old:1234"}
    entry.options = {}
    entry.update_listeners = listeners
    flow._reconfigure_entry = entry
    flow._current_entries = [entry]
    flow.hass = MagicMock()
    flow.hass.config_entries.async_unload = AsyncMock(return_value=True)
    flow.hass.config_entries.async_setup = AsyncMock(return_value=True)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort", "reason": "reloaded"})
    return flow, entry


class TestReconfigure(unittest.TestCase):
    def test_a_loaded_entry_with_a_listener_is_updated_not_reloaded_twice(self):
        flow, _entry = _reconfigure_flow(ConfigEntryState.LOADED, [object()])
        with patch(_TEST_CONN, new=AsyncMock()):
            result = _run(flow.async_step_reconfigure(_form("old:1234")))
        self.assertEqual(result, {"type": "abort", "reason": "reconfigure_successful"})
        flow.async_update_reload_and_abort.assert_not_called()
        flow.hass.config_entries.async_update_entry.assert_called_once()
        kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        self.assertEqual(kwargs["title"], "Nikobus (old:1234)")
        self.assertEqual(kwargs["unique_id"], "old:1234")

    def test_an_unloaded_entry_is_updated_and_reloaded(self):
        flow, _entry = _reconfigure_flow(ConfigEntryState.NOT_LOADED, [])
        with patch(_TEST_CONN, new=AsyncMock()):
            result = _run(flow.async_step_reconfigure(_form("new:5678")))
        self.assertEqual(result["reason"], "reloaded")
        self.assertEqual(
            flow.async_update_reload_and_abort.call_args.kwargs["title"], "Nikobus (new:5678)"
        )

    def test_a_pending_retry_is_cancelled_before_the_port_is_tested(self):
        flow, entry = _reconfigure_flow(ConfigEntryState.SETUP_RETRY, [])
        order: list[str] = []
        entry.async_cancel_retry_setup = lambda: order.append("cancel")
        with patch(_TEST_CONN, new=AsyncMock(side_effect=lambda _h, _c: order.append("test"))):
            _run(flow.async_step_reconfigure(_form("new:5678")))
        self.assertEqual(order, ["cancel", "test"])

    def test_a_failed_unload_does_not_test_the_held_port(self):
        flow, _entry = _reconfigure_flow(ConfigEntryState.LOADED, [object()])
        flow.hass.config_entries.async_unload = AsyncMock(return_value=False)
        test = AsyncMock()
        with patch(_TEST_CONN, new=test):
            result = _run(flow.async_step_reconfigure(_form("new:5678")))
        test.assert_not_awaited()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "unknown"})
        flow.hass.config_entries.async_setup.assert_not_awaited()

    def test_a_cancelled_test_sets_the_old_connection_up_again(self):
        flow, _entry = _reconfigure_flow(ConfigEntryState.LOADED, [object()])
        with (
            patch(_TEST_CONN, new=AsyncMock(side_effect=asyncio.CancelledError())),
            self.assertRaises(asyncio.CancelledError),
        ):
            _run(flow.async_step_reconfigure(_form("new:5678")))
        flow.hass.config_entries.async_setup.assert_awaited_once_with("E1")


# --- the "no buttons" repair while a discovery runs ----------------------------


class TestNoButtonsRepairWhileDiscoveryRuns(unittest.TestCase):
    def test_confirm_is_refused_and_keeps_the_issue(self):
        from custom_components.nikobus.repairs import NoButtonsConfiguredRepairFlow

        flow = NoButtonsConfiguredRepairFlow("E1")
        flow.hass = MagicMock()
        coord = MagicMock()
        coord.discovery_running = True
        with (
            patch("custom_components.nikobus.repairs._coordinator", return_value=coord),
            patch("custom_components.nikobus.button._entry_background_task") as task,
        ):
            result = _run(flow.async_step_confirm({}))
        self.assertEqual(result, {"type": "abort", "reason": "discovery_already_running"})
        task.assert_not_called()


# --- a mistyped motion detector address -----------------------------------------


class TestMotionDetectorWithABadAddress(unittest.TestCase):
    def test_only_that_detector_gets_no_sensors(self):
        store = {
            "0986BC": {"model": "05-7*5", "device_type": "28", "channels": 4, "operation_points": {}},
            "TYPO": {"model": "05-7*5", "device_type": "28", "channels": 4, "operation_points": {}},
        }
        coordinator = MagicMock()
        coordinator.dict_button_data = {"nikobus_button": store}
        coordinator.dict_module_data = {}
        entry = SimpleNamespace(runtime_data=coordinator, options={"button_classes": []}, entry_id="E1")
        added: list = []
        with (
            patch.object(bs, "register_wall_button_devices"),
            self.assertLogs("custom_components.nikobus.binary_sensor", level=logging.WARNING) as logs,
        ):
            _run(bs.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents)))
        ids = {e._attr_unique_id for e in added if isinstance(e, bs.NikobusMotionDetectorSensor)}
        self.assertEqual(ids, {"nikobus_motion_0986bc_occupancy", "nikobus_motion_0986bc_light"})
        self.assertTrue(any("TYPO" in line for line in logs.output))


# --- an LED key must drive its own channel only ---------------------------------


def _coordinator_with_links() -> NikobusDataCoordinator:
    """Key 8E0383 drives C9A5 ch 1 and 0E6C ch 2; key CE0383 drives C9A5 ch 1 only."""
    coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    coord.hass = None
    coord.dict_button_data = {
        "nikobus_button": {
            "0D1C80": {
                "channels": 4,
                "operation_points": {
                    "1A": {
                        "bus_address": "8E0383",
                        "linked_modules": [
                            {"module_address": "C9A5", "outputs": [{"channel": 1, "mode": "M05"}]},
                            {"module_address": "0E6C", "outputs": [{"channel": 2, "mode": "M05"}]},
                        ],
                    },
                    "1B": {
                        "bus_address": "CE0383",
                        "linked_modules": [
                            {"module_address": "c9a5", "outputs": [{"channel": "1", "mode": "M05"}]},
                        ],
                    },
                },
            }
        }
    }
    coord.dict_module_data = {
        "switch_module": {
            "C9A5": {"channels": [{"description": "Counter", "led_on": "8E0383", "led_off": "CE0383"}]}
        },
        "dimmer_module": {"0E6C": {"channels": [{}, {"led_on": "CE0383"}]}},
    }
    return coord


class TestLedKeyConflicts(unittest.TestCase):
    def test_outputs_driven_besides_lists_the_other_links(self):
        coord = _coordinator_with_links()
        self.assertEqual(coord.outputs_driven_besides("8e0383", "c9a5", 1), [("0E6C", 2)])
        self.assertEqual(coord.outputs_driven_besides("CE0383", "C9A5", 1), [])
        self.assertEqual(coord.outputs_driven_besides("FFFFFF", "C9A5", 1), [])

    def test_led_key_conflicts_reports_each_offending_key_once(self):
        coord = _coordinator_with_links()
        self.assertEqual(
            coord.led_key_conflicts(),
            [
                ("C9A5", 1, "8E0383", [("0E6C", 2)]),
                ("0E6C", 2, "CE0383", [("C9A5", 1)]),
            ],
        )

    def test_startup_warns_about_each_conflict(self):
        with self.assertLogs("custom_components.nikobus", level=logging.WARNING) as logs:
            _warn_led_keys_driving_other_outputs(_coordinator_with_links())
        self.assertEqual(len(logs.output), 2)
        self.assertIn("LED key 8E0383 also drives 0E6C ch 2", logs.output[0])


class TestEditChannelRefusesASharedLedKey(unittest.TestCase):
    def _flow(self):
        flow = NikobusOptionsFlow()
        entry = MagicMock()
        entry.state = ConfigEntryState.LOADED
        entry.options = {}
        coordinator = entry.runtime_data
        coordinator.module_storage.data = {
            "nikobus_module": {
                "C9A5": {"module_type": "switch_module", "channels": [{"description": "Counter"}]}
            }
        }
        coordinator.async_on_module_save = AsyncMock()
        flow.config_entry = entry
        flow.hass = MagicMock()
        flow._edit_module_address = "C9A5"
        flow._edit_channel_index = 1
        flow.async_step_edit_module = AsyncMock(return_value={"type": "form", "step_id": "edit_module"})
        return flow, coordinator

    def test_a_key_driving_other_outputs_is_refused(self):
        flow, coordinator = self._flow()
        coordinator.outputs_driven_besides.return_value = [("0E6C", 2)]
        result = _run(flow.async_step_edit_channel({"entity_type": "default", "led_on": "8E0383"}))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "led_key_drives_other_outputs"})
        coordinator.outputs_driven_besides.assert_called_once_with("8E0383", "C9A5", 1)
        coordinator.async_on_module_save.assert_not_awaited()
        self.assertNotIn("led_on", coordinator.module_storage.data["nikobus_module"]["C9A5"]["channels"][0])

    def test_a_key_driving_this_channel_only_is_saved(self):
        flow, coordinator = self._flow()
        coordinator.outputs_driven_besides.return_value = []
        result = _run(flow.async_step_edit_channel({"entity_type": "default", "led_on": "CE0383"}))
        self.assertEqual(result["step_id"], "edit_module")
        channel = coordinator.module_storage.data["nikobus_module"]["C9A5"]["channels"][0]
        self.assertEqual(channel["led_on"], "CE0383")
        coordinator.async_on_module_save.assert_awaited_once()


# --- manifest ----------------------------------------------------------------------


class TestManifest(unittest.TestCase):
    def test_single_entry_and_no_stale_requirement(self):
        path = os.path.join(os.path.dirname(__file__), "..", "custom_components", "nikobus", "manifest.json")
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertTrue(manifest["single_config_entry"])
        self.assertFalse([r for r in manifest["requirements"] if r.startswith("pyserial-asyncio-fast")])
