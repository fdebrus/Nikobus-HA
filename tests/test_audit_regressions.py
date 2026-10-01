"""Regressions from the 2026-10-01 code audit of the integration."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
from nikobus_connect.exceptions import NikobusTimeoutError

from custom_components.nikobus.devices import register_opaque_module_devices
from custom_components.nikobus.nkbprogramming import NikobusProgramming
from custom_components.nikobus.router import audio_zones


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _coordinator(api):
    coord = SimpleNamespace()
    coord.dict_module_data = {
        "pc_link": {"86F5": {"module_type": "pc_link", "description": "PC-Link"}},
        "switch_module": {"9105": {"module_type": "switch_module", "description": "Kitchen"}},
        "dimmer_module": {"4707": {"module_type": "dimmer_module", "description": "Living"}},
    }
    coord.dict_button_data = {"nikobus_button": {}}
    coord.api = api
    coord.nikobus_connection = SimpleNamespace(is_connected=True)
    coord.discovery_running = False
    coord.async_update_listeners = MagicMock()
    return coord


def _api():
    api = MagicMock()
    status = SimpleNamespace(eeprom_error=False, record_count_a=12, record_count_b=0)
    api.get_module_status = AsyncMock(return_value=status)
    api.read_module_memory = AsyncMock(return_value=b"\xff" * 64)
    api.get_module_crc = AsyncMock(return_value=0x1234)
    api.verify_module_memory = AsyncMock(return_value=(True, 0x1234, 0x1234))
    api.get_pc_link_time = AsyncMock(return_value=datetime(2026, 9, 3, 21, 10, 43))  # noqa: DTZ001
    api.set_pc_link_time = AsyncMock()
    return api


def _hass(tmp):
    hass = SimpleNamespace()
    hass.config = SimpleNamespace(path=lambda *parts: str(Path(tmp, *parts)))

    async def executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = executor
    return hass


class TestMaintenanceServices(unittest.TestCase):
    def _prog(self, tmp="/tmp", api=None):
        prog = NikobusProgramming(_hass(tmp), _coordinator(api or _api()))
        prog._store = MagicMock()
        prog._store.async_save = AsyncMock()
        return prog

    def test_verify_persists_the_baseline_it_records(self):
        prog = self._prog()
        with patch("custom_components.nikobus.nkbprogramming.ir"):
            _run(prog.async_verify_modules())
        prog._store.async_save.assert_awaited()
        self.assertEqual(prog.crc_baseline["9105"]["crc"], 0x1234)

    def test_backup_persists_the_baseline_too(self):
        with TemporaryDirectory() as tmp:
            prog = self._prog(tmp)
            with patch("custom_components.nikobus.nkbprogramming.ir"):
                _run(prog.async_backup_modules())
            prog._store.async_save.assert_awaited()

    def test_a_filtered_run_reports_only_its_modules_and_names_the_unknown(self):
        prog = self._prog()
        with patch("custom_components.nikobus.nkbprogramming.ir"):
            _run(prog.async_verify_modules())
            report = _run(prog.async_verify_modules(["9105", "ZZZZ"]))
        self.assertEqual(list(report["modules"]), ["9105"])
        self.assertEqual(report["not_found"], ["ZZZZ"])
        # The earlier result is still kept, just not reported as this run's.
        self.assertIn("4707", prog.checks)

    def test_a_backup_that_cannot_be_written_is_a_translated_error(self):
        with TemporaryDirectory() as tmp:
            blocker = Path(tmp, "nikobus_backup")
            blocker.write_text("not a directory")
            prog = self._prog(tmp)
            with patch("custom_components.nikobus.nkbprogramming.ir"), self.assertRaises(
                HomeAssistantError
            ) as cm:
                _run(prog.async_backup_modules())
        self.assertEqual(cm.exception.translation_key, "backup_write_failed")
        self.assertFalse(prog.running)

    def test_a_silent_pc_link_is_a_translated_error_on_clock_sync(self):
        api = _api()
        api.set_pc_link_time = AsyncMock(side_effect=NikobusTimeoutError("no answer"))
        prog = self._prog(api=api)
        with self.assertRaises(HomeAssistantError) as cm:
            _run(prog.async_sync_clock())
        self.assertEqual(cm.exception.translation_key, "communication_error")

    def test_a_silent_pc_link_is_a_translated_error_on_clock_read(self):
        api = _api()
        api.get_pc_link_time = AsyncMock(side_effect=TimeoutError())
        prog = self._prog(api=api)
        with self.assertRaises(HomeAssistantError) as cm:
            _run(prog.async_read_clock())
        self.assertEqual(cm.exception.translation_key, "communication_error")


def _register(module_data):
    dev_reg = MagicMock()
    dev_reg.async_get_device.return_value = MagicMock(id="PARENT")
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    with patch("custom_components.nikobus.devices.dr.async_get", return_value=dev_reg):
        register_opaque_module_devices(hass, entry, module_data)
    return {
        next(iter(c.kwargs["identifiers"]))[1]: c.kwargs
        for c in dev_reg.async_get_or_create.call_args_list
    }


class TestDeviceRegistration(unittest.TestCase):
    def test_the_imported_name_wins_over_the_discovery_description(self):
        devices = _register(
            {
                "audio_module": {
                    "8334": {
                        "module_type": "audio_module",
                        "description": "audio_module_1",
                        "nkb_name": "Audio salon",
                    }
                }
            }
        )
        self.assertEqual(devices["8334"]["name"], "Audio salon")
        self.assertEqual(devices["8334"]["model"], "05-205")

    def test_an_other_module_gets_a_device_under_its_name(self):
        devices = _register(
            {
                "other_module": {
                    "1234": {
                        "module_type": "other_module",
                        "description": "RGB plinth light",
                        "model": "340-00111",
                    }
                }
            }
        )
        self.assertEqual(devices["1234"]["name"], "RGB plinth light")
        self.assertEqual(devices["1234"]["model"], "340-00111")


class TestAudioZones(unittest.TestCase):
    def test_a_trigger_driving_two_zones_keeps_both(self):
        store = {
            "8083CF": {
                "audio_function": "M16 (On)",
                "audio_zone": 2,
                "operation_points": {
                    "AUD": {
                        "bus_address": "8083CF",
                        "linked_modules": [
                            {
                                "module_address": "8334",
                                "outputs": [
                                    {"channel": 1, "audio_function": "M16 (On)", "audio_zone": 1},
                                    {"channel": 2, "audio_function": "M16 (On)", "audio_zone": 2},
                                ],
                            }
                        ],
                    }
                },
            }
        }
        zones = audio_zones(store)
        self.assertEqual(zones[("8334", 1)], {"M16 (On)": "8083CF"})
        self.assertEqual(zones[("8334", 2)], {"M16 (On)": "8083CF"})

    def test_a_wall_key_driving_a_zone_is_found_on_the_key(self):
        store = {
            "124A36": {
                "description": "Kitchen plate",
                "channels": 4,
                "operation_points": {
                    "1C": {
                        "bus_address": "1B1492",
                        "linked_modules": [
                            {
                                "module_address": "8334",
                                "outputs": [
                                    {"channel": 1, "audio_function": "M13 (Volume up)", "audio_zone": 1}
                                ],
                            }
                        ],
                    }
                },
            }
        }
        self.assertEqual(audio_zones(store), {("8334", 1): {"M13 (Volume up)": "1B1492"}})


class TestDiagnostics(unittest.TestCase):
    def test_channel_count_comes_from_the_channel_list_the_store_keeps(self):
        coord = SimpleNamespace(
            dict_module_data={
                "switch_module": {
                    "C7C1": {"channels": [{"description": f"out {n}"} for n in range(12)]},
                },
            },
            dict_button_data={
                "nikobus_button": {
                    "020080": {
                        "operation_points": {
                            "1A": {
                                "linked_modules": [
                                    {
                                        "module_address": "C7C1",
                                        "outputs": [{"channel": 1, "mode": "M01 (On / off)"}],
                                    }
                                ]
                            }
                        }
                    }
                }
            },
        )
        from test_diagnostics_quality import _load_diagnostics

        metrics = _load_diagnostics()._per_module_decode_metrics(coord)
        self.assertEqual(metrics["C7C1"]["channel_count"], 12)
        self.assertEqual(metrics["C7C1"]["channels_without_links"], 11)
