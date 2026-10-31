"""Fixes from the second audit of 3.30.6 (3.30.7)."""

from __future__ import annotations

import asyncio
import json
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus import async_setup_entry, nkbmanual
from custom_components.nikobus.config_flow import NikobusConfigFlow, NikobusOptionsFlow
from custom_components.nikobus.const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
)
from custom_components.nikobus.nkbstorage import NikobusModuleStorage

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "nikobus"
_TEST_CONN = "custom_components.nikobus.config_flow._test_connection"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- options flow: store edits reload, a not-loaded entry is refused --------


def _options_flow(state=ConfigEntryState.LOADED, options=None):
    flow = NikobusOptionsFlow()
    entry = MagicMock()
    entry.entry_id = "E1"
    entry.state = state
    entry.data = {}
    entry.options = dict(options or {})
    flow.config_entry = entry
    flow.hass = MagicMock()
    flow.hass.config_entries.async_reload = MagicMock(return_value="reload-coro")
    return flow, entry


class TestStoreEditsReload(unittest.TestCase):
    def test_unchanged_options_schedule_a_reload(self):
        flow, _entry = _options_flow(options={"x": 1})
        result = flow._finish_after_store_edit()
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {"x": 1})
        flow.hass.config_entries.async_reload.assert_called_once_with("E1")
        flow.hass.async_create_task.assert_called_once_with("reload-coro")

    def test_changed_options_leave_the_reload_to_the_listener(self):
        flow, _entry = _options_flow(options={"x": 1})
        flow._options = {"x": 2}
        result = flow._finish_after_store_edit()
        self.assertEqual(result["data"], {"x": 2})
        flow.hass.async_create_task.assert_not_called()

    def test_scene_delete_reloads(self):
        flow, entry = _options_flow()
        coord = MagicMock()
        coord.dict_scene_data = {"scene": [{"id": "s1"}, {"id": "s2"}]}
        coord.nikobus_config.save_json_data = AsyncMock()
        entry.runtime_data = coord
        flow._scene_work = {"id": "s1"}
        result = _run(flow._delete_scene())
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(coord.dict_scene_data["scene"], [{"id": "s2"}])
        flow.hass.config_entries.async_reload.assert_called_once_with("E1")


class TestNotLoadedEntryIsRefused(unittest.TestCase):
    def test_coordinator_is_none_until_loaded(self):
        flow, entry = _options_flow(state="setup_retry")
        entry.runtime_data = MagicMock()
        self.assertIsNone(flow._coordinator())
        entry.state = ConfigEntryState.LOADED
        self.assertIs(flow._coordinator(), entry.runtime_data)

    def test_scene_editor_aborts_instead_of_saving_over_the_file(self):
        flow, entry = _options_flow(state="setup_retry")
        entry.runtime_data = MagicMock()
        result = _run(flow.async_step_manage_scenes(None))
        self.assertEqual(result, {"type": "abort", "reason": "not_loaded"})

    def test_nkb_import_aborts(self):
        flow, entry = _options_flow(state="setup_retry")
        entry.runtime_data = MagicMock()
        result = _run(flow.async_step_import_nkb({"names": True}))
        self.assertEqual(result, {"type": "abort", "reason": "not_loaded"})


# --- reconfigure --------------------------------------------------------------


def _reconfigure_flow(state, current="old:1234", others=()):
    flow = NikobusConfigFlow()
    entry = MagicMock()
    entry.entry_id = "E1"
    entry.state = state
    entry.data = {CONF_CONNECTION_STRING: current}
    entry.options = {}
    flow._reconfigure_entry = entry
    flow._current_entries = [entry, *others]
    flow.hass = MagicMock()
    flow.hass.config_entries.async_unload = AsyncMock(return_value=True)
    flow.hass.config_entries.async_setup = AsyncMock(return_value=True)
    captured: dict = {}

    def _update(entry_, **kw):
        captured.update(kw)
        return {"type": "abort", "reason": "reconfigure_successful"}

    flow.async_update_reload_and_abort = _update
    return flow, entry, captured


def _form(connection):
    return {
        CONF_CONNECTION_STRING: connection,
        CONF_HAS_FEEDBACK_MODULE: False,
        CONF_PRIOR_GEN3: False,
        CONF_REFRESH_INTERVAL: 120,
    }


class TestReconfigureWhileLoaded(unittest.TestCase):
    def test_unchanged_connection_is_not_tested_against_the_held_port(self):
        flow, _entry, captured = _reconfigure_flow(ConfigEntryState.LOADED)
        test = AsyncMock()
        with patch(_TEST_CONN, new=test):
            result = _run(flow.async_step_reconfigure(_form("old:1234")))
        self.assertEqual(result["reason"], "reconfigure_successful")
        test.assert_not_awaited()
        flow.hass.config_entries.async_unload.assert_not_awaited()
        self.assertEqual(captured["unique_id"], "old:1234")

    def test_new_connection_unloads_first_then_tests(self):
        flow, _entry, captured = _reconfigure_flow(ConfigEntryState.LOADED)
        order: list[str] = []
        flow.hass.config_entries.async_unload = AsyncMock(side_effect=lambda _id: order.append("unload"))
        test = AsyncMock(side_effect=lambda _h, _c: order.append("test"))
        with patch(_TEST_CONN, new=test):
            result = _run(flow.async_step_reconfigure(_form("New:5678")))
        self.assertEqual(order, ["unload", "test"])
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(captured["unique_id"], "new:5678")
        flow.hass.config_entries.async_setup.assert_not_awaited()  # the reload sets it up

    def test_failed_test_sets_the_old_connection_up_again(self):
        flow, entry, _captured = _reconfigure_flow(ConfigEntryState.LOADED)
        with patch(_TEST_CONN, new=AsyncMock(side_effect=ValueError("nope"))):
            result = _run(flow.async_step_reconfigure(_form("bad:0")))
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        flow.hass.config_entries.async_setup.assert_awaited_once_with("E1")
        self.assertEqual(entry.data, {CONF_CONNECTION_STRING: "old:1234"})

    def test_connection_of_another_entry_is_refused(self):
        other = MagicMock(entry_id="E2", unique_id="host:9999")
        flow, _entry, _captured = _reconfigure_flow(ConfigEntryState.LOADED, others=[other])
        test = AsyncMock()
        with patch(_TEST_CONN, new=test):
            result = _run(flow.async_step_reconfigure(_form("HOST:9999")))
        self.assertEqual(result["errors"], {"base": "already_configured"})
        test.assert_not_awaited()
        flow.hass.config_entries.async_unload.assert_not_awaited()


# --- setup releases the port on any connect failure --------------------------


class TestSetupReleasesThePort(unittest.TestCase):
    def test_non_nikobus_error_in_connect_stops_the_coordinator(self):
        coord = MagicMock()
        coord.connect = AsyncMock(side_effect=HomeAssistantError("init boom"))
        coord.stop = AsyncMock()
        with (
            patch("custom_components.nikobus.NikobusDataCoordinator", return_value=coord),
            self.assertRaises(HomeAssistantError),
        ):
            _run(async_setup_entry(MagicMock(), MagicMock()))
        coord.stop.assert_awaited_once()


# --- repair flow -------------------------------------------------------------


class TestNoButtonsRepairRunsInTheBackground(unittest.TestCase):
    def test_confirm_starts_the_inventory_and_closes(self):
        from custom_components.nikobus.repairs import NoButtonsConfiguredRepairFlow

        flow = NoButtonsConfiguredRepairFlow("E1")
        flow.hass = MagicMock()
        flow.async_create_entry = lambda **kw: {"type": "create_entry"}
        coord = MagicMock()
        coord.start_pc_link_inventory = AsyncMock()
        started: list = []
        with (
            patch("custom_components.nikobus.repairs._coordinator", return_value=coord),
            patch(
                "custom_components.nikobus.button._entry_background_task",
                side_effect=lambda hass, c, coro, name: started.append((coro, name)),
            ),
        ):
            result = _run(flow.async_step_confirm({}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(len(started), 1)
        coro, name = started[0]
        self.assertEqual(name, "nikobus_repair_pc_link_inventory")
        _run(coro)
        coord.start_pc_link_inventory.assert_awaited_once()

    def test_a_refusal_is_logged_not_raised(self):
        from custom_components.nikobus.repairs import _run_inventory_logged

        coord = MagicMock()
        coord.start_pc_link_inventory = AsyncMock(side_effect=HomeAssistantError("busy"))
        with self.assertLogs("custom_components.nikobus.repairs", level=logging.WARNING):
            _run(_run_inventory_logged(coord))


# --- query_module_inventory goes through the coordinator ----------------------


class TestInventoryServiceUsesTheCoordinator(unittest.TestCase):
    def _call(self, data):
        from custom_components.nikobus import (
            SERVICE_QUERY_MODULE_INVENTORY,
            async_setup,
        )

        hass = MagicMock()
        registered: dict = {}
        hass.services.async_register = (
            lambda domain, name, handler, schema=None, **_kw: registered.__setitem__(name, handler)
        )
        coord = MagicMock()
        coord.discovery_running = False
        coord.start_pc_link_inventory = MagicMock(return_value="inventory-coro")
        coord.start_module_scan = MagicMock(return_value="scan-coro")
        coord.nikobus_discovery.query_module_inventory = AsyncMock()
        started: list = []
        with (
            patch("custom_components.nikobus._loaded_coordinator", return_value=coord),
            patch(
                "custom_components.nikobus.button._entry_background_task",
                side_effect=lambda h, c, coro, name: started.append(coro),
            ),
        ):
            _run(async_setup(hass, {}))
            _run(registered[SERVICE_QUERY_MODULE_INVENTORY](MagicMock(data=data)))
        return coord, started

    def test_no_address_runs_the_inventory_wrapper(self):
        coord, started = self._call({})
        self.assertEqual(started, ["inventory-coro"])
        coord.nikobus_discovery.start_inventory_discovery.assert_not_called()

    def test_an_address_runs_the_module_scan_wrapper(self):
        coord, started = self._call({"module_address": " c9a5 "})
        coord.start_module_scan.assert_called_once_with("C9A5")
        self.assertEqual(started, ["scan-coro"])
        coord.nikobus_discovery.query_module_inventory.assert_not_awaited()

    def test_a_register_range_still_talks_to_the_library(self):
        coord, started = self._call(
            {"module_address": "C9A5", "register_start": 0x10, "register_end": 0x20}
        )
        self.assertEqual(started, [])
        coord.nikobus_discovery.query_module_inventory.assert_awaited_once()


# --- manual module file -------------------------------------------------------


def _store():
    store = NikobusModuleStorage.__new__(NikobusModuleStorage)
    store._data = {
        "nikobus_module": {
            "C9A5": {"module_type": "switch_module", "nkb_name": "Salon", "channels": []}
        }
    }
    return store


def _apply(payload):
    store = _store()
    with patch.object(
        nkbmanual, "_read_json", AsyncMock(return_value=("/config/nikobus_module_config.json", payload))
    ):
        result = _run(nkbmanual._apply_module_config(MagicMock(), store))
    return result, store.data["nikobus_module"]


class TestManualModuleFile(unittest.TestCase):
    def test_a_file_with_no_readable_module_leaves_the_store(self):
        for payload in ({"nikobus_module": {}}, {"switch_module": "oops"}, {}):
            with self.assertLogs("custom_components.nikobus.nkbmanual", level=logging.WARNING):
                result, modules = _apply(payload)
            self.assertEqual(result[0], 0)
            self.assertEqual(modules["C9A5"]["nkb_name"], "Salon")

    def test_a_storage_shaped_file_keeps_each_modules_type(self):
        (count, _path), modules = _apply(
            {"nikobus_module": {"c9a5": {"module_type": "roller_module"}, "4707": {"module_type": "dimmer_module"}}}
        )
        self.assertEqual(count, 2)
        self.assertEqual(modules["C9A5"]["module_type"], "roller_module")
        self.assertEqual(modules["4707"]["module_type"], "dimmer_module")

    def test_the_group_key_still_wins_in_the_documented_shape(self):
        (_count, _path), modules = _apply({"switch_module": [{"address": "C9A5", "module_type": "x"}]})
        self.assertEqual(modules["C9A5"]["module_type"], "switch_module")

    def test_duplicates_and_unknown_types_are_reported(self):
        with self.assertLogs("custom_components.nikobus.nkbmanual", level=logging.WARNING) as logs:
            _apply(
                {
                    "switch_module": [{"address": "C9A5"}, {"address": "c9a5"}],
                    "relay_module": [{"address": "1234"}],
                }
            )
        text = "\n".join(logs.output)
        self.assertIn("C9A5 is listed more than once", text)
        self.assertIn("1234", text)

    def test_channel_count_written_as_text_is_accepted(self):
        entry = {"linked_button": [{"address": "0d1c80", "key": "1a", "channels": "4"}]}
        self.assertEqual(nkbmanual._extract_physical_info(entry)[:3], ("0D1C80", "1A", 4))
        entry["linked_button"][0]["channels"] = True
        self.assertIsNone(nkbmanual._extract_physical_info(entry))


class TestManualFilesAreWrittenAtomically(unittest.TestCase):
    def test_a_failed_write_leaves_no_partial_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            hass = MagicMock()
            hass.config.path = lambda name: str(Path(tmp) / name)

            async def _executor(func, *args):
                return func(*args)

            hass.async_add_executor_job = _executor
            config = SimpleNamespace(module_config={"switch_module": []}, button_config={"nikobus_button": []})
            nkb = Path(tmp) / "project.nkb"
            nkb.write_bytes(b"x")
            with (
                patch.object(nkbmanual, "_read_json", AsyncMock(return_value=(None, None))),
                patch.object(nkbmanual, "find_nkb_file", return_value=nkb, create=True),
                patch.object(nkbmanual, "build_manual_config", return_value=config, create=True),
                patch.object(nkbmanual.os, "replace", side_effect=OSError("disk full")),
            ):
                try:
                    _run(nkbmanual._generate_manual_files_from_nkb(hass))
                except OSError:
                    pass
            leftovers = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(leftovers, ["project.nkb"])


# --- cover ---------------------------------------------------------------------


def _cover():
    from custom_components.nikobus.cover import NikobusCoverEntity

    coord = MagicMock()
    coord.api.open_cover = AsyncMock()
    coord.api.close_cover = AsyncMock()
    coord.api.stop_cover = AsyncMock()
    ent = NikobusCoverEntity(coord, "9105", 1, "Shutter", "Roller module", "05-001", 10.0, 10.0, 3.0)
    ent.hass = MagicMock()

    def _create_task(arg):
        if asyncio.iscoroutine(arg):
            arg.close()
        return MagicMock()

    ent.hass.async_create_task = MagicMock(side_effect=_create_task)
    ent.async_write_ha_state = MagicMock()
    return ent, coord


class TestCoverEndStopMargin(unittest.TestCase):
    def _after_full_open(self, bus_state):
        from custom_components.nikobus.cover import STATE_STOPPED

        ent, coord = _cover()
        ent._state = STATE_STOPPED
        ent._position = 100.0
        ent._last_motion_direction = "opening"
        pending = MagicMock()
        pending.done.return_value = False
        ent._end_stop_task = pending
        coord.get_cover_state = MagicMock(return_value=bus_state)
        async def _update() -> None:  # HA runs this callback in the event loop
            ent._handle_coordinator_update()

        with patch.object(ent, "_start_motion_logic"):
            _run(_update())
        return ent, pending

    def test_wall_motion_the_other_way_drops_the_pending_stop(self):
        from custom_components.nikobus.cover import STATE_CLOSING

        ent, pending = self._after_full_open(STATE_CLOSING)
        pending.cancel.assert_called_once()
        self.assertIsNone(ent._end_stop_task)

    def test_our_own_direction_still_engaged_keeps_it(self):
        from custom_components.nikobus.cover import STATE_OPENING

        ent, pending = self._after_full_open(STATE_OPENING)
        pending.cancel.assert_not_called()
        self.assertIs(ent._end_stop_task, pending)


class TestCoverMotionIsOursAfterTheAck(unittest.TestCase):
    def test_on_sent_reclaims_the_motion(self):
        ent, coord = _cover()
        _run(ent._request_move("closing", 50))
        on_sent = coord.api.close_cover.await_args.args[2]
        ent._movement_source = "nikobus"  # a pushed 0x02 arrived before the ack
        with patch.object(ent, "_start_motion_logic") as start:
            _run(on_sent())
        self.assertEqual(ent._movement_source, "ha")
        start.assert_called_once_with("closing")
        self.assertEqual(ent._target_position, 50)


# --- button operation event ---------------------------------------------------


def test_initial_press_operation_event_is_pressed_without_duration():
    from custom_components.nikobus.const import EVENT_BUTTON_OPERATION
    from tests.test_actuator_burst import _events_of, _make_actuator_linked_to

    actuator = _make_actuator_linked_to("4707", "switch_module")
    asyncio.run(actuator.button_discovery("8083CF", press_context={"press_id": "p1", "duration_s": 0.0}))
    asyncio.run(actuator.button_discovery("8083CF", press_context={"press_id": "p1", "duration_s": 0.4}))
    events = _events_of(actuator, EVENT_BUTTON_OPERATION)
    assert [(e["state"], e["duration_s"]) for e in events] == [("pressed", None), ("released", 0.4)]


# --- clock sync ----------------------------------------------------------------


def test_clock_sync_refuses_while_a_scan_runs():
    from custom_components.nikobus.nkbprogramming import NikobusProgramming

    coordinator = SimpleNamespace(discovery_running=True, api=MagicMock())
    prog = NikobusProgramming(MagicMock(), coordinator)
    with pytest.raises(HomeAssistantError) as err:
        asyncio.run(prog.async_sync_clock())
    assert err.value.translation_key == "discovery_already_running"
    coordinator.api.set_pc_link_time.assert_not_called()


# --- translations ------------------------------------------------------------


def test_new_abort_and_error_keys_exist_in_every_language():
    for lang in ("en", "fr", "nl"):
        data = json.loads((PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        assert "reconfigure_successful" in data["config"]["abort"], lang
        assert "already_configured" in data["config"]["error"], lang
        assert "no_modules" in data["options"]["abort"], lang
        assert "not_loaded" in data["issues"]["no_buttons_configured"]["fix_flow"]["abort"], lang
