"""Programming maintenance: link-derived run times, clock, verify, backup."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

from custom_components.nikobus.nkbprogramming import (
    HEALTH_OK,
    HEALTH_PROBLEM,
    HEALTH_UNKNOWN,
    NikobusProgramming,
    link_run_time,
    parse_run_time_label,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRunTimeLabels(unittest.TestCase):
    def test_seconds_and_minutes(self):
        self.assertEqual(parse_run_time_label("30 s"), 30.0)
        self.assertEqual(parse_run_time_label("1 m"), 60.0)
        self.assertEqual(parse_run_time_label("90 s"), 90.0)

    def test_non_travel_labels(self):
        self.assertIsNone(parse_run_time_label("0,4 s (impuls)"))  # sub-second impulse
        self.assertIsNone(parse_run_time_label("S_DB_PUSHTIME_OFF"))
        self.assertIsNone(parse_run_time_label(None))


def _buttons(*links):
    return {
        "nikobus_button": {
            "1A2B3C": {
                "operation_points": {
                    "1A": {"bus_address": "081032", "linked_modules": list(links)},
                }
            }
        }
    }


class TestLinkRunTime(unittest.TestCase):
    def test_direction_specific_max(self):
        buttons = _buttons(
            {"module_address": "9105", "channel": 2, "mode": "M02 (Open)", "t1": "20 s"},
            {"module_address": "9105", "channel": 2, "mode": "M01 (Open - stop - close)", "t1": "25 s"},
            {"module_address": "9105", "channel": 2, "mode": "M03 (Close)", "t1": "40 s"},
            {"module_address": "9105", "channel": 3, "mode": "M03 (Close)", "t1": "90 s"},
        )
        self.assertEqual(link_run_time(buttons, "9105", 2, "up"), 25.0)
        self.assertEqual(link_run_time(buttons, "9105", 2, "down"), 40.0)
        self.assertEqual(link_run_time(buttons, "9105", 3, "up"), None)

    def test_ignores_other_modules_and_modes(self):
        buttons = _buttons(
            {"module_address": "4707", "channel": 2, "mode": "M02 (Open)", "t1": "20 s"},
            {"module_address": "9105", "channel": 2, "mode": "M04 (Stop)", "t1": "20 s"},
        )
        self.assertIsNone(link_run_time(buttons, "9105", 2, "up"))
        self.assertIsNone(link_run_time(None, "9105", 2, "up"))


def _coordinator(modules=None, api=None):
    coord = SimpleNamespace()
    coord.dict_module_data = modules or {
        "pc_link": {"86F5": {"module_type": "pc_link", "description": "PC-Link"}},
        "switch_module": {"9105": {"module_type": "switch_module", "description": "Kitchen"}},
        "dimmer_module": {"4707": {"module_type": "dimmer_module", "description": "Living"}},
        "other_module": {"0E6C": {"module_type": "other_module"}},
    }
    coord.dict_button_data = {"nikobus_button": {}}
    coord.api = api
    coord.nikobus_connection = SimpleNamespace(is_connected=True)
    coord.discovery_running = False
    coord.async_update_listeners = MagicMock()
    return coord


def _api(eeprom_error=False, crc_ok=True):
    api = MagicMock()
    api.get_module_status = AsyncMock(
        return_value=SimpleNamespace(eeprom_error=eeprom_error, record_count_a=12, record_count_b=0)
    )
    api.read_module_memory = AsyncMock(return_value=b"\xff" * 64)
    api.verify_module_memory = AsyncMock(return_value=(crc_ok, 0x1234, 0x1234 if crc_ok else 0x9999))
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


class TestInventoryHelpers(unittest.TestCase):
    def test_pc_link_and_output_modules(self):
        prog = NikobusProgramming(_hass("/tmp"), _coordinator())
        self.assertEqual(prog.pc_link_address(), "86F5")
        self.assertEqual(
            sorted(a for a, _t, _d in prog.output_modules()), ["4707", "9105"]
        )
        self.assertEqual(prog.health, HEALTH_UNKNOWN)


class TestClock(unittest.TestCase):
    def test_read_and_sync(self):
        api = _api()
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=api))
        clock = _run(prog.async_read_clock())
        self.assertEqual((clock.year, clock.minute, clock.second), (2026, 10, 43))
        self.assertIsNotNone(prog.clock_drift_seconds)
        _run(prog.async_sync_clock())
        api.set_pc_link_time.assert_awaited_once()
        sent = api.set_pc_link_time.await_args.args[1]
        self.assertIsNone(sent.tzinfo)  # controller keeps naive local time

    def test_unset_clock_is_none(self):
        api = _api()
        api.get_pc_link_time = AsyncMock(side_effect=ValueError("unset"))
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=api))
        self.assertIsNone(_run(prog.async_read_clock()))


class TestVerifyAndBackup(unittest.TestCase):
    def test_verify_reports_ok(self):
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=_api()))
        report = _run(prog.async_verify_modules())
        self.assertEqual(report["health"], HEALTH_OK)
        self.assertEqual(set(report["modules"]), {"9105", "4707"})
        self.assertEqual(report["modules"]["9105"]["record_count_a"], 12)
        self.assertFalse(prog.running)

    def test_verify_flags_problems(self):
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=_api(crc_ok=False)))
        report = _run(prog.async_verify_modules(["9105"]))
        self.assertEqual(report["health"], HEALTH_PROBLEM)
        self.assertEqual(list(report["modules"]), ["9105"])
        self.assertFalse(report["modules"]["9105"]["crc_ok"])

    def test_module_failure_does_not_abort_run(self):
        api = _api()
        api.get_module_status = AsyncMock(side_effect=[RuntimeError("timeout"), api.get_module_status.return_value])
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=api))
        report = _run(prog.async_verify_modules())
        statuses = {m["status"] for m in report["modules"].values()}
        self.assertEqual(statuses, {HEALTH_UNKNOWN, HEALTH_OK})

    def test_backup_writes_images_and_summary(self):
        with TemporaryDirectory() as tmp:
            prog = NikobusProgramming(_hass(tmp), _coordinator(api=_api()))
            result = _run(prog.async_backup_modules())
            folder = Path(result["path"])
            self.assertTrue(folder.is_dir())
            self.assertEqual(
                sorted(p.name for p in folder.iterdir()),
                ["4707_dimmer_module.nkm", "9105_switch_module.nkm", "summary.json"],
            )
            self.assertEqual((folder / "9105_switch_module.nkm").read_bytes(), b"\xff" * 64)
            summary = json.loads((folder / "summary.json").read_text())
            self.assertEqual(summary["health"], HEALTH_OK)
            self.assertEqual(prog.last_backup_path, str(folder))

    def test_refuses_while_discovery_runs(self):
        coord = _coordinator(api=_api())
        coord.discovery_running = True
        prog = NikobusProgramming(_hass("/tmp"), coord)
        with self.assertRaises(HomeAssistantError):
            _run(prog.async_verify_modules())


class TestLinkRunTimeStoredShape(unittest.TestCase):
    """Discovery stores links as ``{module_address, outputs: [...]}``."""

    def test_reads_outputs_list(self):
        buttons = {
            "nikobus_button": {
                "1A2B3C": {
                    "operation_points": {
                        "1A": {
                            "bus_address": "081032",
                            "linked_modules": [
                                {
                                    "module_address": "9105",
                                    "outputs": [
                                        {"channel": 2, "mode": "M02 (Open)", "t1": "20 s"},
                                        {"channel": 2, "mode": "M03 (Close)", "t1": "35 s"},
                                    ],
                                }
                            ],
                        }
                    }
                }
            }
        }
        self.assertEqual(link_run_time(buttons, "9105", 2, "up"), 20.0)
        self.assertEqual(link_run_time(buttons, "9105", 2, "down"), 35.0)



if __name__ == "__main__":
    unittest.main()
