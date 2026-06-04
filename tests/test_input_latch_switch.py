"""Tests for the PC-Logic / Modular-Interface input A/B latch switch."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from custom_components.nikobus.switch import input_ab_addresses
from custom_components.nikobus.coordinator import NikobusDataCoordinator


class TestInputABAddresses(unittest.TestCase):
    """``input_ab_addresses`` must match the library's derivation
    (validated against the documented 0x940C install):

        slot 1 -> 1A 21814B / 1B 61814B
        slot 4 -> 1A 09814B / 1B 49814B
    """

    def _phys(self, slot, parent="940C", ptype="pc_logic"):
        return {
            "pc_logic_parent_address": parent,
            "pc_logic_slot_index": slot,
            "pc_logic_parent_type": ptype,
        }

    def test_slot1(self):
        self.assertEqual(input_ab_addresses(self._phys(1)), ("21814B", "61814B"))

    def test_slot4(self):
        self.assertEqual(input_ab_addresses(self._phys(4)), ("09814B", "49814B"))

    def test_1b_is_first_nibble_plus_four(self):
        a, b = input_ab_addresses(self._phys(1))
        self.assertEqual(int(b[0], 16), (int(a[0], 16) + 4) % 16)
        self.assertEqual(a[1:], b[1:])

    def test_interface_module_uses_same_derivation(self):
        # interface_module inputs derive identically (the library uses
        # the same helper for both types).
        self.assertEqual(
            input_ab_addresses(self._phys(1, ptype="interface_module")),
            ("21814B", "61814B"),
        )

    def test_missing_provenance_returns_none(self):
        self.assertIsNone(input_ab_addresses({}))
        self.assertIsNone(input_ab_addresses({"pc_logic_parent_address": "940C"}))


class TestInputSwitchKnownIds(unittest.TestCase):
    """The latch switch's unique_id must be in the known set so the
    orphan cleanup doesn't evict it."""

    def test_input_switch_ids_are_known(self):
        coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
        coord.dict_module_data = {}
        coord.dict_scene_data = {}
        coord.cf_storage = MagicMock()
        coord.cf_storage.data = {"nikobus_cf": {}}
        coord.dict_button_data = {
            "nikobus_button": {
                "64A061": {
                    "pc_logic_parent_address": "940C",
                    "pc_logic_slot_index": 1,
                    "pc_logic_parent_type": "pc_logic",
                },
                # an ordinary wall button must NOT get an input-switch id
                "1DF1E0": {"type": "Wall Button"},
            }
        }

        known = coord.get_known_entity_unique_ids()

        # Must match switch.py: f"nikobus_input_switch_{addr.lower()}"
        self.assertIn("nikobus_input_switch_64a061", known)
        self.assertNotIn("nikobus_input_switch_1df1e0", known)


class TestInputChildHelpers(unittest.TestCase):
    """Shared router helpers that the switch platform and the known-id
    set both build on — they must agree on predicate and id format."""

    def test_is_input_module_child(self):
        from custom_components.nikobus.router import is_input_module_child

        self.assertTrue(is_input_module_child({"pc_logic_parent_type": "pc_logic"}))
        self.assertTrue(
            is_input_module_child({"pc_logic_parent_type": "interface_module"})
        )
        self.assertFalse(is_input_module_child({"type": "Wall Button"}))
        self.assertFalse(is_input_module_child(None))

    def test_unique_id_format(self):
        from custom_components.nikobus.router import input_latch_switch_unique_id

        self.assertEqual(
            input_latch_switch_unique_id("64A061"), "nikobus_input_switch_64a061"
        )

    def test_iter_yields_only_input_children(self):
        from custom_components.nikobus.router import iter_input_module_children

        buttons = {
            "64A061": {"pc_logic_parent_type": "pc_logic"},
            "1DF1E0": {"type": "Wall Button"},
            "0E1234": {"pc_logic_parent_type": "interface_module"},
        }
        got = {addr for addr, _ in iter_input_module_children(buttons)}
        self.assertEqual(got, {"64A061", "0E1234"})
        self.assertEqual(list(iter_input_module_children(None)), [])


class TestIterOperationPoints(unittest.TestCase):
    """Shared op-point enumerator — one guard ladder for the button /
    binary-sensor platforms and the known-id set."""

    def test_yields_op_points_with_bus_address(self):
        from custom_components.nikobus.router import iter_operation_points

        buttons = {
            "1A2B3C": {
                "operation_points": {
                    "1A": {"bus_address": "081032"},
                    "1B": {"bus_address": "481032"},
                }
            }
        }
        got = [(a, k, op["bus_address"]) for a, k, op, _ in iter_operation_points(buttons)]
        self.assertEqual(
            got, [("1A2B3C", "1A", "081032"), ("1A2B3C", "1B", "481032")]
        )

    def test_skips_op_point_without_bus_address(self):
        from custom_components.nikobus.router import iter_operation_points

        buttons = {"X": {"operation_points": {"1A": {"description": "no bus addr"}}}}
        self.assertEqual(list(iter_operation_points(buttons)), [])

    def test_non_dict_operation_points_is_safe(self):
        # The guard that binary_sensor / the known-id loop previously
        # lacked: a list-shaped operation_points must not raise.
        from custom_components.nikobus.router import iter_operation_points

        buttons = {
            "BAD": {"operation_points": [{"bus_address": "1"}]},  # list, not dict
            "OK": {"operation_points": {"1A": {"bus_address": "AABBCC"}}},
        }
        got = [op["bus_address"] for _, _, op, _ in iter_operation_points(buttons)]
        self.assertEqual(got, ["AABBCC"])

    def test_non_dict_entry_and_none_are_safe(self):
        from custom_components.nikobus.router import iter_operation_points

        self.assertEqual(list(iter_operation_points(None)), [])
        self.assertEqual(list(iter_operation_points({"X": "not-a-dict"})), [])


if __name__ == "__main__":
    unittest.main()
