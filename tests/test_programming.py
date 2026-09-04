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
from nikobus_connect.discovery.feedback_decoder import (
    FEEDBACK_IMAGE_SIZE,
    LED_MODE_TABLE_OFFSET,
    REGION_GROUP_ADDRESSES,
    REGION_LED_LISTS,
    REGION_OUTPUT_MODULES,
    decode_feedback_image,
)

from custom_components.nikobus.nkbprogramming import (
    HEALTH_OK,
    HEALTH_PROBLEM,
    HEALTH_UNKNOWN,
    LED_SOURCE_FEEDBACK,
    NikobusProgramming,
    link_run_time,
    parse_run_time_label,
    resolve_feedback_led,
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


def _feedback_image() -> bytes:
    """Plate 1843B4 (4 keys, group 0): slot 0 tracks 5B05 ch 1, slot 3
    tracks 5B05 ch 6 and dimmer 0E6C ch 2; own LED 1 tracks 0E6C ch 2."""
    img = bytearray(b"\xff" * FEEDBACK_IMAGE_SIZE)
    base = REGION_OUTPUT_MODULES[0]
    img[base : base + 8] = bytes([1, 0x5B, 0x05, 0, 0x00, 0x21, 0xFF, 0xFF])
    img[base + 8 : base + 16] = bytes([3, 0x0E, 0x6C, 0, 0x00, 0x02, 0xFF, 0xFF])
    grp = REGION_GROUP_ADDRESSES[0]
    img[grp : grp + 3] = bytes([0x06, 0x10, 0xEC])  # 1843B4 with bit 0 cleared
    lists = REGION_LED_LISTS[0]
    stream = bytes(
        [0x00, 0x00, 0x04, 0x00, 0x00, 0x01, 0x00, 0x02, 0x04, 0x03, 0x00, 0x02, 0x04, 0xC0]
    )
    img[lists : lists + len(stream)] = stream
    img[LED_MODE_TABLE_OFFSET] = 0
    img[LED_MODE_TABLE_OFFSET + 3] = 0
    return bytes(img)


def _plate(links: dict[str, list[tuple[str, int]]]) -> dict:
    """Plate 1843B4 with its four real key addresses and the given links."""
    addresses = {"1A": "8B7086", "1B": "CB7086", "1C": "0B7086", "1D": "4B7086"}
    return {
        "1843B4": {
            "type": "Button",
            "operation_points": {
                key: {
                    "bus_address": addr,
                    "linked_modules": [
                        {"module_address": m, "outputs": [{"channel": c, "mode": "M01 (On / off)"}]}
                        for m, c in links.get(key, [])
                    ],
                }
                for key, addr in addresses.items()
            },
        }
    }


class TestResolveFeedbackLed(unittest.TestCase):
    def _led(self, slot):
        decoded = decode_feedback_image(_feedback_image())
        return next(led for led in decoded.leds if led.slot == slot)

    def test_links_single_out_the_key(self):
        buttons = _plate({"1A": [("5B05", 1)], "1B": [("5B05", 6)]})
        self.assertEqual(
            resolve_feedback_led(self._led(0), [("5B05", 1)], buttons),
            ("1843B4", "1A", "8B7086"),
        )
        self.assertEqual(
            resolve_feedback_led(self._led(3), [("5B05", 6), ("0E6C", 2)], buttons),
            ("1843B4", "1B", "CB7086"),
        )

    def test_row_order_decides_without_links(self):
        buttons = _plate({})
        # slot 0 = row 0 = 1D on a 4-key plate, slot 3 = 1A
        self.assertEqual(resolve_feedback_led(self._led(0), [("5B05", 1)], buttons)[1], "1D")
        self.assertEqual(resolve_feedback_led(self._led(3), [("5B05", 6)], buttons)[1], "1A")

    def test_unknown_plate_is_unresolved(self):
        self.assertIsNone(resolve_feedback_led(self._led(0), [("5B05", 1)], {}))


def _import_coordinator(api, channels_5b05=None, links=None):
    coord = _coordinator(
        modules={
            "switch_module": {"5B05": {"module_type": "switch_module", "description": "S2"}},
            "dimmer_module": {"0E6C": {"module_type": "dimmer_module", "description": "D1"}},
            "feedback_module": {"966C": {"module_type": "feedback_module", "description": "FB"}},
        },
        api=api,
    )
    coord.dict_button_data = {"nikobus_button": _plate(links or {"1A": [("5B05", 1)], "1B": [("5B05", 6)]})}
    coord.module_storage = SimpleNamespace(
        data={
            "nikobus_module": {
                "5B05": {
                    "module_type": "switch_module",
                    "channels": channels_5b05
                    or [{"description": f"out {i}"} for i in range(1, 13)],
                },
                "0E6C": {"module_type": "dimmer_module", "channels": [{"description": f"d {i}"} for i in range(1, 13)]},
            }
        }
    )
    coord.async_on_module_save = AsyncMock()
    return coord


class TestImportFeedbackLeds(unittest.TestCase):
    def test_fills_led_addresses_and_saves(self):
        api = _api()
        api.read_module_memory = AsyncMock(return_value=_feedback_image())
        coord = _import_coordinator(api)
        prog = NikobusProgramming(_hass("/tmp"), coord)
        report = _run(prog.async_import_feedback_leds())
        api.read_module_memory.assert_awaited_once_with("966C", "feedback_module")
        self.assertEqual((report["leds"], report["resolved"]), (3, 2))  # own LED has no plate
        self.assertEqual(report["channels_updated"], 3)
        ch = coord.module_storage.data["nikobus_module"]["5B05"]["channels"]
        self.assertEqual((ch[0]["led_on"], ch[0]["led_off"], ch[0]["led_source"]), ("8B7086", "8B7086", LED_SOURCE_FEEDBACK))
        self.assertEqual(ch[5]["led_on"], "CB7086")
        dimmer = coord.module_storage.data["nikobus_module"]["0E6C"]["channels"]
        self.assertEqual(dimmer[1]["led_on"], "CB7086")
        coord.async_on_module_save.assert_awaited_once()
        self.assertEqual(len(report["unresolved"]), 1)  # the module's own LED
        self.assertFalse(prog.running)

    def test_keeps_typed_addresses_unless_overwrite(self):
        api = _api()
        api.read_module_memory = AsyncMock(return_value=_feedback_image())
        channels = [{"description": f"out {i}"} for i in range(1, 13)]
        channels[0]["led_on"] = "AAAAAA"
        coord = _import_coordinator(api, channels_5b05=channels)
        prog = NikobusProgramming(_hass("/tmp"), coord)
        report = _run(prog.async_import_feedback_leds())
        self.assertEqual(channels[0]["led_on"], "AAAAAA")
        self.assertEqual(report["channels_kept"], 1)
        report = _run(prog.async_import_feedback_leds(overwrite=True))
        self.assertEqual(channels[0]["led_on"], "8B7086")
        # The two channels imported on the first run already hold the
        # address: unchanged, so counted as kept.
        self.assertEqual((report["channels_updated"], report["channels_kept"]), (1, 2))

    def test_without_feedback_module_raises(self):
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=_api()))
        with self.assertRaises(HomeAssistantError):
            _run(prog.async_import_feedback_leds())

    def test_backup_includes_feedback_module_without_crc(self):
        api = _api()
        api.get_module_status = AsyncMock(side_effect=RuntimeError("no answer"))
        coord = _import_coordinator(api)
        with TemporaryDirectory() as tmp:
            prog = NikobusProgramming(_hass(tmp), coord)
            result = _run(prog.async_backup_modules(["966C"]))
            self.assertIn("966C_feedback_module.nkm", result["images"])
            check = result["modules"]["966C"]
            self.assertIsNone(check["crc_ok"])
            self.assertIsNone(check["error"])
            api.verify_module_memory.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class TestReadBlocks(unittest.TestCase):
    def test_reads_and_reports_timeouts_with_link_mode(self):
        from nikobus_connect.exceptions import NikobusTimeoutError

        api = _api()
        api.set_link_mode = AsyncMock()
        calls: list[tuple[int, bytes]] = []

        async def query(func, address, args=None):
            calls.append((func, args))
            if args == b"\x00\x06":
                raise NikobusTimeoutError("Failed to receive ACK and state")
            return bytes.fromhex("6C96") + bytes(range(16))

        api._command_handler = SimpleNamespace(query=query)
        coord = _import_coordinator(api)
        prog = NikobusProgramming(_hass("/tmp"), coord)
        result = _run(prog.async_read_blocks("966c", [0, 0x600], link_mode=True))
        self.assertEqual(result["address"], "966C")
        self.assertEqual(result["blocks"]["0x0000"], bytes(range(16)).hex().upper())
        self.assertTrue(result["blocks"]["0x0600"].startswith("timeout"))
        self.assertEqual([c[0] for c in calls], [0x10, 0x10])
        self.assertEqual(
            [c.args for c in api.set_link_mode.await_args_list],
            [("966C", True), ("966C", False)],
        )
        self.assertFalse(prog.running)

    def test_eight_byte_function(self):
        api = _api()
        seen = []

        async def query(func, address, args=None):
            seen.append(func)
            return bytes.fromhex("6C96") + b"\x01" * 8

        api._command_handler = SimpleNamespace(query=query)
        prog = NikobusProgramming(_hass("/tmp"), _import_coordinator(api))
        result = _run(prog.async_read_blocks("966C", ["0xC00"], block_size=8))
        self.assertEqual(seen, [0x22])
        self.assertEqual(result["blocks"]["0x0C00"], "01" * 8)
