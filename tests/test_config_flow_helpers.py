"""Tests for the pure helpers in config_flow.py.

The flow steps need HA's flow framework; these cover the deterministic
logic helpers (hex validation, type ordering, default channels,
set-or-drop, int coercion, polling decision).
"""

from __future__ import annotations

import unittest

import voluptuous as vol

from custom_components.nikobus.config_flow import (
    _validate_optional_hex6,
    _module_type_order,
    _make_default_channel,
    _module_label,
    _set_or_drop,
    _set_time_or_drop,
    _coerce_int,
    _needs_polling,
)
from custom_components.nikobus.const import CONF_HAS_FEEDBACK_MODULE, CONF_PRIOR_GEN3


class TestValidateOptionalHex6(unittest.TestCase):
    def test_empty_and_none_pass(self):
        self.assertEqual(_validate_optional_hex6(None), "")
        self.assertEqual(_validate_optional_hex6(""), "")
        self.assertEqual(_validate_optional_hex6("   "), "")

    def test_valid_normalized_upper(self):
        self.assertEqual(_validate_optional_hex6("  1a2b3c "), "1A2B3C")

    def test_invalid_raises(self):
        with self.assertRaises(vol.Invalid):
            _validate_optional_hex6("12345")  # too short
        with self.assertRaises(vol.Invalid):
            _validate_optional_hex6("GGGGGG")  # non-hex


class TestModuleTypeOrder(unittest.TestCase):
    def test_known_and_unknown(self):
        self.assertEqual(_module_type_order("switch_module"), 0)
        self.assertEqual(_module_type_order("dimmer_module"), 1)
        self.assertEqual(_module_type_order("roller_module"), 2)
        self.assertEqual(_module_type_order("pc_logic"), 99)
        self.assertEqual(_module_type_order(None), 99)


class TestMakeDefaultChannel(unittest.TestCase):
    def test_output_label(self):
        self.assertEqual(
            _make_default_channel("switch_module", 5),
            {"description": "not_in_use output_5"},
        )

    def test_roller_gets_operation_time(self):
        ch = _make_default_channel("roller_module", 2)
        self.assertEqual(ch["operation_time_up"], "30")

    def test_input_label_for_pc_logic(self):
        self.assertEqual(
            _make_default_channel("pc_logic", 1),
            {"description": "not_in_use input_1"},
        )


class TestModuleLabel(unittest.TestCase):
    def test_label(self):
        label = _module_label(
            "3851", {"description": "Kitchen", "module_type": "switch_module"}
        )
        self.assertEqual(label, "Switch Module — 3851 — Kitchen")

    def test_label_defaults(self):
        self.assertEqual(_module_label("ABCD", {}), "Module — ABCD — Module ABCD")


class TestSetOrDrop(unittest.TestCase):
    def test_sets_when_truthy(self):
        m = {}
        _set_or_drop(m, "k", "v")
        self.assertEqual(m, {"k": "v"})

    def test_drops_when_empty(self):
        m = {"k": "old"}
        _set_or_drop(m, "k", "")
        self.assertEqual(m, {})


class TestSetTimeOrDrop(unittest.TestCase):
    def test_valid_stored_as_str(self):
        m = {}
        _set_time_or_drop(m, "t", 12.7)
        self.assertEqual(m, {"t": "12"})

    def test_drops_on_empty_zero_negative_invalid(self):
        for bad in ("", None, 0, -5, "abc"):
            m = {"t": "old"}
            _set_time_or_drop(m, "t", bad)
            self.assertEqual(m, {}, bad)


class TestCoerceInt(unittest.TestCase):
    def test_values(self):
        self.assertEqual(_coerce_int("5", 1), 5)
        self.assertEqual(_coerce_int(3.9, 1), 3)
        self.assertEqual(_coerce_int("abc", 7), 7)
        self.assertEqual(_coerce_int(None, 7), 7)


class TestNeedsPolling(unittest.TestCase):
    def test_feedback_or_gen3_skip_polling(self):
        self.assertFalse(_needs_polling({CONF_HAS_FEEDBACK_MODULE: True}))
        self.assertFalse(_needs_polling({CONF_PRIOR_GEN3: True}))

    def test_plain_install_needs_polling(self):
        self.assertTrue(_needs_polling({}))


if __name__ == "__main__":
    unittest.main()
