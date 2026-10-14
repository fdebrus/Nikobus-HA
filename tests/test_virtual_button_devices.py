"""Devices for buttons that exist only in programming (library 0.52.0).

A PC-Logic logic output hangs under its PC-Logic, next to the module's
inputs; a key of the software's virtual input banks goes under the
*Virtual buttons* category. Both are gone when the class is unticked.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from custom_components.nikobus.const import (
    BUTTON_CLASS_VIRTUAL_BUTTONS,
    BUTTON_CLASS_WALL_BUTTONS,
    CATEGORY_DEVICES,
    CATEGORY_SYSTEM_MODULES,
    CATEGORY_VIRTUAL_BUTTONS,
    DOMAIN,
)
from custom_components.nikobus.devices import register_wall_button_devices
from custom_components.nikobus.router import button_class, virtual_button_naming

L01 = {
    "description": "PC-Logic virtual output 684100",
    "type": "Virtual button",
    "model": "Virtual",
    "channels": 4,
    "virtual_button": True,
    "virtual_class": "pc_logic",
    "virtual_parent_address": "8DC8",
    "operation_points": {
        "1C": {"bus_address": "002087", "linked_modules": [{"module_address": "81F6", "outputs": [{"channel": 6}]}]},
        "1D": {"bus_address": "402087", "linked_modules": [{"module_address": "81F6", "outputs": [{"channel": 6}]}]},
    },
}
BANK = {
    "description": "Virtual button 3CF006",
    "type": "Virtual button",
    "model": "Virtual",
    "channels": 4,
    "virtual_button": True,
    "virtual_class": "bank",
    "operation_points": {"1C": {"bus_address": "1803CF"}},
}
MODULE_DATA = {"pc_logic": {"8DC8": {"description": "PC-Logic", "model": "05-201"}}}


def _register(store, classes=None, module_data=None):
    dev_reg = MagicMock()
    dev_reg.async_get_device_by_identifier.return_value = MagicMock(id="PARENT")
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    with patch("custom_components.nikobus.devices.dr.async_get", return_value=dev_reg):
        register_wall_button_devices(hass, entry, store, module_data or {}, classes=classes)
    return {
        next(iter(c.kwargs["identifiers"]))[1]: c.kwargs
        for c in dev_reg.async_get_or_create.call_args_list
    }, dev_reg


class TestNaming(unittest.TestCase):
    def test_logic_output_hangs_under_its_pc_logic(self):
        self.assertEqual(
            virtual_button_naming(L01),
            ("PC-Logic virtual output 684100", (DOMAIN, "8DC8")),
        )

    def test_bank_key_goes_under_the_category(self):
        self.assertEqual(
            virtual_button_naming(BANK),
            ("Virtual button 3CF006", (DOMAIN, CATEGORY_VIRTUAL_BUTTONS)),
        )

    def test_project_file_name_wins(self):
        self.assertEqual(virtual_button_naming({**BANK, "nkb_name": "Scene bank"})[0], "Scene bank")

    def test_anything_else_is_not_virtual(self):
        self.assertIsNone(virtual_button_naming({"type": "Bus push button", "channels": 4}))
        self.assertIsNone(virtual_button_naming(None))

    def test_class_is_the_virtual_buttons_one(self):
        self.assertEqual(button_class(L01, "1C"), BUTTON_CLASS_VIRTUAL_BUTTONS)
        self.assertEqual(button_class(BANK, "1C"), BUTTON_CLASS_VIRTUAL_BUTTONS)

    def test_the_category_device_exists(self):
        self.assertIn(CATEGORY_VIRTUAL_BUTTONS, {c[0] for c in CATEGORY_DEVICES})


class TestRegistration(unittest.TestCase):
    def test_logic_output_device_and_its_pc_logic_parent(self):
        devices, dev_reg = _register({"684100": L01}, module_data=MODULE_DATA)
        self.assertEqual(set(devices), {"8DC8", "684100"})
        self.assertEqual(devices["684100"]["name"], "PC-Logic virtual output 684100")
        self.assertEqual(devices["684100"]["model"], "Virtual")
        # The parent was pre-registered, under the system modules, from the module store.
        self.assertEqual(devices["8DC8"]["model"], "05-201")
        # Parent lookups: the PC-Logic's for the output, the category's for the PC-Logic.
        looked_up = [call.args[0] for call in dev_reg.async_get_device_by_identifier.call_args_list]
        self.assertIn((DOMAIN, "8DC8"), looked_up)
        self.assertIn((DOMAIN, CATEGORY_SYSTEM_MODULES), looked_up)

    def test_bank_key_device_under_the_category(self):
        devices, dev_reg = _register({"3CF006": BANK})
        self.assertEqual(set(devices), {"3CF006"})
        self.assertEqual(devices["3CF006"]["name"], "Virtual button 3CF006")
        looked_up = [call.args[0] for call in dev_reg.async_get_device_by_identifier.call_args_list]
        self.assertIn((DOMAIN, CATEGORY_VIRTUAL_BUTTONS), looked_up)

    def test_unticked_class_registers_nothing(self):
        devices, _ = _register(
            {"684100": L01, "3CF006": BANK},
            classes=frozenset({BUTTON_CLASS_WALL_BUTTONS}),
            module_data=MODULE_DATA,
        )
        self.assertEqual(devices, {})

    def test_ticked_class_registers_both(self):
        devices, _ = _register(
            {"684100": L01, "3CF006": BANK},
            classes=frozenset({BUTTON_CLASS_VIRTUAL_BUTTONS}),
            module_data=MODULE_DATA,
        )
        self.assertEqual(set(devices), {"8DC8", "684100", "3CF006"})
