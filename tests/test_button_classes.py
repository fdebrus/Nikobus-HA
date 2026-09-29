"""The press-entity classes option: what exists, what disappears, what comes back.

Three layers, each pinned:

* the classifier — which class a store entry's op points belong to;
* the enumerators and device registration — a deselected class yields
  nothing, so its entities are not created and its devices not
  registered, and the known-id set stops claiming them so the orphan
  cleanup removes what was there;
* the snapshot — names, icons, areas and entity ids survive a class
  being switched off and on again.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from custom_components.nikobus.const import (
    BUTTON_CLASS_AUDIO_TRIGGERS,
    BUTTON_CLASS_INPUT_MODULES,
    BUTTON_CLASS_INTERFACES,
    BUTTON_CLASS_REMOTES,
    BUTTON_CLASS_VIRTUAL_BUTTONS,
    BUTTON_CLASS_WALL_BUTTONS,
    BUTTON_CLASSES,
    CONF_BUTTON_CLASSES,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONFIG_ENTRY_VERSION,
)
from custom_components.nikobus.devices import register_wall_button_devices
from custom_components.nikobus.nkbsnapshot import (
    DEVICE_SNAPSHOT_KEY,
    ENTITY_SNAPSHOT_KEY,
    restore_devices,
    restore_entities,
    snapshot_device,
    snapshot_entity,
    unique_id_index,
)
from custom_components.nikobus.router import (
    button_class,
    enabled_button_classes,
    entry_button_classes,
    iter_input_module_children,
    iter_operation_points,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


PLATE = {
    "type": "Bus push button, 4 control buttons",
    "model": "05-064",
    "operation_points": {"1A": {"bus_address": "804E2C"}, "1B": {"bus_address": "C04E2C"}},
}
IR_PLATE = {
    "type": "Push button, 4 control buttons with IR receiver",
    "model": "05-09x",
    "operation_points": {"1A": {"bus_address": "8D1C80"}, "IR:30A": {"bus_address": "DE4E2C"}},
}
INTERFACE = {"type": "Switch interface", "model": "05-057", "operation_points": {"1A": {"bus_address": "9B418F"}}}
REMOTE = {"type": "RF868 Mini-zender, 4 bedieningspunten", "model": "05-314", "operation_points": {"1A": {"bus_address": "BFB6F6"}}}
TRANSMITTER_CHILD = {"remote_transmitter_address": "RT-E31C", "remote_transmitter_bus_address": "12E31C", "operation_points": {"1A": {"bus_address": "12E31C"}}}
INPUT = {"pc_logic_parent_address": "80D9", "pc_logic_parent_type": "pc_logic", "pc_logic_slot_index": 1, "operation_points": {"1A": {"bus_address": "20D80B"}}}
AUDIO = {"type": "Audio Trigger", "audio_function": "M16 (On)", "audio_module_address": "8334", "operation_points": {"AUD": {"bus_address": "8083CF"}}}
VIRTUAL = {"virtual_button": True, "operation_points": {"1A": {"bus_address": "1803CF"}}}

STORE = {
    "0D1C80": PLATE,
    "0D1C90": IR_PLATE,
    "3C60B6": INTERFACE,
    "1BDB7F": REMOTE,
    "12E31C": TRANSMITTER_CHILD,
    "6406C1": INPUT,
    "8083CF": AUDIO,
    "3CF006": VIRTUAL,
}


# --- the classifier ---------------------------------------------------------


class TestButtonClass(unittest.TestCase):
    def test_each_kind_of_entry_lands_in_its_class(self):
        self.assertEqual(button_class(PLATE, "1A"), BUTTON_CLASS_WALL_BUTTONS)
        self.assertEqual(button_class(INTERFACE, "1A"), BUTTON_CLASS_INTERFACES)
        self.assertEqual(button_class(REMOTE, "1A"), BUTTON_CLASS_REMOTES)
        self.assertEqual(button_class(TRANSMITTER_CHILD, "1A"), BUTTON_CLASS_REMOTES)
        self.assertEqual(button_class(INPUT, "1A"), BUTTON_CLASS_INPUT_MODULES)
        self.assertEqual(button_class(AUDIO, "AUD"), BUTTON_CLASS_AUDIO_TRIGGERS)
        self.assertEqual(button_class(VIRTUAL, "1A"), BUTTON_CLASS_VIRTUAL_BUTTONS)

    def test_an_ir_receivers_keys_and_codes_split_by_op_point(self):
        self.assertEqual(button_class(IR_PLATE, "1A"), BUTTON_CLASS_WALL_BUTTONS)
        self.assertEqual(button_class(IR_PLATE, "IR:30A"), BUTTON_CLASS_REMOTES)
        self.assertEqual(
            entry_button_classes(IR_PLATE),
            frozenset({BUTTON_CLASS_WALL_BUTTONS, BUTTON_CLASS_REMOTES}),
        )

    def test_an_entry_with_no_op_points_still_has_a_class(self):
        self.assertEqual(entry_button_classes({"type": "Switch interface"}), frozenset({BUTTON_CLASS_INTERFACES}))
        self.assertEqual(button_class(None), BUTTON_CLASS_WALL_BUTTONS)


class TestEnabledClasses(unittest.TestCase):
    def test_no_option_at_all_means_every_class(self):
        # The pre-3.22.0 behaviour, and what the migration writes out.
        self.assertEqual(enabled_button_classes(None), frozenset(BUTTON_CLASSES))
        self.assertEqual(enabled_button_classes({}), frozenset(BUTTON_CLASSES))

    def test_an_empty_list_means_none(self):
        self.assertEqual(enabled_button_classes({CONF_BUTTON_CLASSES: []}), frozenset())

    def test_unknown_values_are_ignored(self):
        got = enabled_button_classes({CONF_BUTTON_CLASSES: ["wall_buttons", "typo"]})
        self.assertEqual(got, frozenset({BUTTON_CLASS_WALL_BUTTONS}))


# --- the enumerators and device registration --------------------------------


class TestEnumerators(unittest.TestCase):
    def test_no_filter_yields_everything(self):
        self.assertEqual(len(list(iter_operation_points(STORE))), 10)
        self.assertEqual(len(list(iter_input_module_children(STORE))), 1)

    def test_a_deselected_class_yields_nothing(self):
        only_wall = frozenset({BUTTON_CLASS_WALL_BUTTONS})
        got = [(a, k) for a, k, _op, _p in iter_operation_points(STORE, only_wall)]
        self.assertEqual(got, [("0D1C80", "1A"), ("0D1C80", "1B"), ("0D1C90", "1A")])
        self.assertEqual(list(iter_input_module_children(STORE, only_wall)), [])
        self.assertEqual(len(list(iter_input_module_children(STORE, frozenset({BUTTON_CLASS_INPUT_MODULES})))), 1)

    def test_nothing_selected_yields_nothing(self):
        self.assertEqual(list(iter_operation_points(STORE, frozenset())), [])


def _registered_devices(classes):
    dev_reg = MagicMock()
    dev_reg.async_get_device.return_value = MagicMock(id="PARENT")
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    with patch("custom_components.nikobus.devices.dr.async_get", return_value=dev_reg):
        register_wall_button_devices(hass, entry, STORE, {}, classes=classes)
    return {next(iter(c.kwargs["identifiers"]))[1] for c in dev_reg.async_get_or_create.call_args_list}


class TestDeviceRegistration(unittest.TestCase):
    def test_a_deselected_class_registers_no_device(self):
        devices = _registered_devices(frozenset({BUTTON_CLASS_WALL_BUTTONS}))
        self.assertIn("0D1C80", devices)
        self.assertIn("0D1C90", devices)  # the IR receiver is also a wall plate
        for gone in ("3C60B6", "1BDB7F", "12E31C", "6406C1", "8083CF", "3CF006"):
            self.assertNotIn(gone, devices)
        # No child means no synthesised parent either.
        self.assertNotIn("80D9", devices)
        self.assertNotIn("8334", devices)

    def test_nothing_selected_registers_nothing(self):
        self.assertEqual(_registered_devices(frozenset()), set())

    def test_no_filter_registers_everything(self):
        self.assertTrue({"0D1C80", "3C60B6", "1BDB7F", "6406C1", "8083CF"} <= _registered_devices(None))


# --- the snapshot -------------------------------------------------------------


def _entry(unique_id, entity_id, **fields):
    e = MagicMock()
    e.unique_id, e.entity_id = unique_id, entity_id
    for f in ("name", "icon", "area_id"):
        setattr(e, f, fields.get(f))
    return e


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        import copy

        self.store = copy.deepcopy(STORE)

    def test_a_removed_entity_leaves_its_customisations_on_the_op_point(self):
        index = unique_id_index(self.store)
        entry = _entry("nikobus_button_804E2C", "binary_sensor.bt_gf_living_sofa_shutter_open", name="Sofa shutter open", area_id="living_room")
        self.assertTrue(snapshot_entity(index, entry))
        saved = self.store["0D1C80"]["operation_points"]["1A"][ENTITY_SNAPSHOT_KEY]["binary_sensor"]
        self.assertEqual(saved, {"entity_id": "binary_sensor.bt_gf_living_sofa_shutter_open", "name": "Sofa shutter open", "area_id": "living_room"})

    def test_a_true_orphan_is_not_snapshotted(self):
        index = unique_id_index(self.store)
        self.assertFalse(snapshot_entity(index, _entry("nikobus_push_button_FFFFFF", "button.gone")))

    def test_a_removed_device_leaves_its_name_and_area_on_the_entry(self):
        device = MagicMock(identifiers={("nikobus", "0D1C80")}, name_by_user="Canapé", area_id="salon")
        self.assertTrue(snapshot_device(self.store, device))
        self.assertEqual(self.store["0D1C80"][DEVICE_SNAPSHOT_KEY], {"name_by_user": "Canapé", "area_id": "salon"})

    def test_a_device_with_nothing_custom_is_not_snapshotted(self):
        device = MagicMock(identifiers={("nikobus", "0D1C80")}, name_by_user=None, area_id=None)
        self.assertFalse(snapshot_device(self.store, device))
        self.assertNotIn(DEVICE_SNAPSHOT_KEY, self.store["0D1C80"])

    def test_restore_puts_the_entity_id_and_name_back_and_drops_the_copy(self):
        op = self.store["0D1C80"]["operation_points"]["1A"]
        op[ENTITY_SNAPSHOT_KEY] = {"binary_sensor": {"entity_id": "binary_sensor.bt_sofa", "name": "Sofa"}}
        ent_reg = MagicMock()
        ent_reg.async_get_entity_id.side_effect = lambda d, p, uid: "binary_sensor.discovered_nikobus_button_n804e2c" if uid == "nikobus_button_804E2C" else None
        ent_reg.async_get.return_value = None  # the wanted id is free
        n = restore_entities(ent_reg, self.store, frozenset({BUTTON_CLASS_WALL_BUTTONS}))
        self.assertEqual(n, 1)
        ent_reg.async_update_entity.assert_called_once_with(
            "binary_sensor.discovered_nikobus_button_n804e2c",
            name="Sofa",
            new_entity_id="binary_sensor.bt_sofa",
        )
        self.assertNotIn(ENTITY_SNAPSHOT_KEY, op)

    def test_restore_waits_while_the_entity_is_not_back_yet(self):
        op = self.store["0D1C80"]["operation_points"]["1A"]
        op[ENTITY_SNAPSHOT_KEY] = {"button": {"entity_id": "button.x"}}
        ent_reg = MagicMock()
        ent_reg.async_get_entity_id.return_value = None
        self.assertEqual(restore_entities(ent_reg, self.store, frozenset(BUTTON_CLASSES)), 0)
        self.assertIn(ENTITY_SNAPSHOT_KEY, op)  # kept for a later pass

    def test_restore_skips_a_class_that_is_still_off(self):
        op = self.store["0D1C80"]["operation_points"]["1A"]
        op[ENTITY_SNAPSHOT_KEY] = {"button": {"entity_id": "button.x"}}
        ent_reg = MagicMock()
        self.assertEqual(restore_entities(ent_reg, self.store, frozenset({BUTTON_CLASS_REMOTES})), 0)
        ent_reg.async_get_entity_id.assert_not_called()

    def test_restore_does_not_steal_an_entity_id_that_is_taken(self):
        op = self.store["0D1C80"]["operation_points"]["1A"]
        op[ENTITY_SNAPSHOT_KEY] = {"button": {"entity_id": "button.taken", "icon": "mdi:x"}}
        ent_reg = MagicMock()
        ent_reg.async_get_entity_id.return_value = "button.current"
        ent_reg.async_get.return_value = MagicMock()  # someone else has button.taken
        restore_entities(ent_reg, self.store, frozenset({BUTTON_CLASS_WALL_BUTTONS}))
        ent_reg.async_update_entity.assert_called_once_with("button.current", icon="mdi:x")

    def test_restore_puts_the_device_name_and_area_back(self):
        self.store["0D1C80"][DEVICE_SNAPSHOT_KEY] = {"name_by_user": "Canapé", "area_id": "salon"}
        dev_reg = MagicMock()
        dev_reg.async_get_device.return_value = MagicMock(id="D1")
        self.assertEqual(restore_devices(dev_reg, self.store, frozenset({BUTTON_CLASS_WALL_BUTTONS})), 1)
        dev_reg.async_update_device.assert_called_once_with("D1", name_by_user="Canapé", area_id="salon")
        self.assertNotIn(DEVICE_SNAPSHOT_KEY, self.store["0D1C80"])


# --- the option in the flow, and the migration ------------------------------


class TestOptionsAndMigration(unittest.TestCase):
    def _flow(self, options):
        from custom_components.nikobus.config_flow import NikobusOptionsFlow

        flow = NikobusOptionsFlow()
        entry = MagicMock()
        entry.data = {CONF_HAS_FEEDBACK_MODULE: True}
        entry.options = options
        flow.config_entry = entry
        return flow

    def test_the_form_defaults_to_what_is_selected(self):
        result = _run(self._flow({CONF_BUTTON_CLASSES: ["remotes"]}).async_step_button_entities(None))
        self.assertEqual(result["type"], "form")
        (key,) = list(result["data_schema"])
        self.assertEqual(key.default(), ["remotes"])

    def test_submitting_keeps_the_other_options(self):
        flow = self._flow({"nkb_import_overwrite": True, CONF_BUTTON_CLASSES: ["remotes"]})
        result = _run(flow.async_step_button_entities({CONF_BUTTON_CLASSES: ["wall_buttons", "typo"]}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {"nkb_import_overwrite": True, CONF_BUTTON_CLASSES: ["wall_buttons"]})

    def test_saving_hardware_no_longer_discards_the_other_options(self):
        flow = self._flow({CONF_BUTTON_CLASSES: [], "nkb_import_overwrite": True})
        result = _run(flow.async_step_hardware({CONF_HAS_FEEDBACK_MODULE: True, CONF_PRIOR_GEN3: False}))
        self.assertEqual(result["data"][CONF_BUTTON_CLASSES], [])
        self.assertTrue(result["data"]["nkb_import_overwrite"])
        self.assertTrue(result["data"][CONF_HAS_FEEDBACK_MODULE])

    def test_a_version_1_entry_keeps_every_class(self):
        from custom_components.nikobus import async_migrate_entry

        hass, entry = MagicMock(), MagicMock()
        entry.version, entry.options = 1, {"nkb_import_overwrite": True}
        self.assertTrue(_run(async_migrate_entry(hass, entry)))
        hass.config_entries.async_update_entry.assert_called_once_with(
            entry,
            options={"nkb_import_overwrite": True, CONF_BUTTON_CLASSES: list(BUTTON_CLASSES)},
            version=CONFIG_ENTRY_VERSION,
        )

    def test_a_current_entry_is_left_alone(self):
        from custom_components.nikobus import async_migrate_entry

        hass, entry = MagicMock(), MagicMock()
        entry.version = CONFIG_ENTRY_VERSION
        self.assertTrue(_run(async_migrate_entry(hass, entry)))
        hass.config_entries.async_update_entry.assert_not_called()
