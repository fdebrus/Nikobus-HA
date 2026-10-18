"""A dimmer's power-loss memory must not read as a reprogramming.

A 05-008-02 writes state into 0x7E3..0x7F9 when power drops, inside the
range its CRC covers. On a real install (2026-10) two power cuts moved
the CRC of two dimmers whose link tables were unchanged, and the daily
check raised "was reprogrammed". The baseline now keeps those bytes; a
CRC move they explain exactly (nikobus-connect ``predict_module_crc``)
updates the baseline silently. Figures: dimmer C95D on that install.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from custom_components.nikobus.nkbprogramming import HEALTH_OK, NikobusProgramming
from tests.test_programming import _api, _coordinator, _hass, _run

START = 0x7E3
BEFORE = bytes.fromhex("FFFFFFFFFFFFFFFFFFFFFF000000000000000000000000")  # 25-09, CRC 0xC28A
AFTER_CUT_1 = bytes.fromhex("62E5FFFFFFFFFFFFFFFFFF000000000000000000000000")  # CRC 0x46D6
AFTER_CUT_2 = bytes.fromhex("62E5FFFFFFFFFFFFFFFFFF006200000000000000000000")  # CRC 0x46CA

MODULES = {"dimmer_module": {"C95D": {"module_type": "dimmer_module", "description": "Dimmer Module D1"}}}


def _prog(crc: int, window: bytes):
    api = _api()
    api.get_module_crc = AsyncMock(return_value=crc)
    api.read_module_volatile = AsyncMock(return_value={START: window})
    return NikobusProgramming(_hass("/tmp"), _coordinator(api=api, modules=MODULES)), api


class TestPowerCut(unittest.TestCase):
    def test_the_baseline_keeps_the_power_loss_bytes(self):
        prog, _ = _prog(0xC28A, BEFORE)
        with patch("custom_components.nikobus.nkbprogramming.ir"):
            _run(prog.async_check_programming_changes())
        baseline = prog.crc_baseline["C95D"]
        self.assertEqual(baseline["crc"], 0xC28A)
        self.assertEqual(baseline["volatile"], {"07E3": BEFORE.hex().upper()})

    def test_two_power_cuts_raise_nothing_and_follow_the_baseline(self):
        prog, api = _prog(0xC28A, BEFORE)
        with patch("custom_components.nikobus.nkbprogramming.ir"):
            _run(prog.async_check_programming_changes())
        for crc, window in ((0x46D6, AFTER_CUT_1), (0x46CA, AFTER_CUT_2)):
            api.get_module_crc = AsyncMock(return_value=crc)
            api.read_module_volatile = AsyncMock(return_value={START: window})
            with patch("custom_components.nikobus.nkbprogramming.ir") as ir:
                result = _run(prog.async_check_programming_changes())
            self.assertEqual(result["changed"], [])
            ir.async_create_issue.assert_not_called()
            self.assertEqual(prog.crc_baseline["C95D"]["crc"], crc)

    def test_a_crc_the_power_loss_bytes_do_not_explain_is_a_reprogramming(self):
        prog, api = _prog(0xC28A, BEFORE)
        with patch("custom_components.nikobus.nkbprogramming.ir"):
            _run(prog.async_check_programming_changes())
        api.get_module_crc = AsyncMock(return_value=0x9999)
        api.read_module_volatile = AsyncMock(return_value={START: AFTER_CUT_1})
        with patch("custom_components.nikobus.nkbprogramming.ir") as ir:
            result = _run(prog.async_check_programming_changes())
        self.assertEqual(result["changed"], ["C95D"])
        ir.async_create_issue.assert_called_once()
        self.assertEqual(prog.crc_baseline["C95D"]["crc"], 0xC28A)

    def test_a_baseline_from_before_the_update_still_raises(self):
        """No stored bytes: nothing to compare, the old behaviour stands."""
        prog, api = _prog(0xC28A, BEFORE)
        prog._crc_baseline = {"C95D": {"crc": 0xC28A, "description": "Dimmer Module D1"}}
        api.get_module_crc = AsyncMock(return_value=0x46D6)
        with patch("custom_components.nikobus.nkbprogramming.ir") as ir:
            result = _run(prog.async_check_programming_changes())
        self.assertEqual(result["changed"], ["C95D"])
        ir.async_create_issue.assert_called_once()

    def test_an_unanswered_read_of_the_bytes_still_raises(self):
        prog, api = _prog(0xC28A, BEFORE)
        with patch("custom_components.nikobus.nkbprogramming.ir"):
            _run(prog.async_check_programming_changes())
        api.get_module_crc = AsyncMock(return_value=0x46D6)
        api.read_module_volatile = AsyncMock(side_effect=TimeoutError())
        with patch("custom_components.nikobus.nkbprogramming.ir") as ir:
            result = _run(prog.async_check_programming_changes())
        self.assertEqual(result["changed"], ["C95D"])
        ir.async_create_issue.assert_called_once()


class TestUnknownFamily(unittest.TestCase):
    def test_an_unknown_family_byte_is_not_a_problem(self):
        prog = NikobusProgramming(_hass("/tmp"), _coordinator(api=_api(type_code=0x77), modules=MODULES))
        with patch("custom_components.nikobus.nkbprogramming.ir") as ir:
            report = _run(prog.async_verify_modules(["C95D"]))
        self.assertIsNone(report["modules"]["C95D"]["family_ok"])
        self.assertIsNone(report["modules"]["C95D"]["family"])
        self.assertEqual(report["health"], HEALTH_OK)
        keys = [c.kwargs.get("translation_key") for c in ir.async_create_issue.call_args_list]
        self.assertNotIn("module_type_mismatch", keys)
