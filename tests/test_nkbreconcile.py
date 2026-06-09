"""Unit tests for the pure reconciliation helpers (nkbreconcile)."""

from __future__ import annotations

from custom_components.nikobus.nkbreconcile import (
    all_outputs_registry_sourced,
    build_controlled_by_index,
    cf_member_set,
    collect_button_outputs,
    has_pc_logic_module,
    member_set_from_outputs,
)


def test_member_set_from_outputs_keys_on_module_channel_modecode():
    outputs = [
        {"module_address": "0e6c", "channel": 2, "mode": "M12 (Preset on)"},
        {"module_address": "C9A5", "channel": 1, "mode": "M04"},
        {"module_address": "0e6c", "channel": 3, "mode": "MCF"},  # no M-code -> dropped
        {"channel": 1, "mode": "M01"},                            # no module -> dropped
        "garbage",                                                # not a dict -> dropped
    ]
    assert member_set_from_outputs(outputs) == frozenset(
        {("0E6C", 2, "M12"), ("C9A5", 1, "M04")}
    )


def test_member_set_from_outputs_empty():
    assert member_set_from_outputs(None) == frozenset()
    assert member_set_from_outputs([]) == frozenset()


def test_cf_member_set_reads_outputs_field():
    cf = {"outputs": [{"module_address": "8394", "channel": 1, "mode": "M02 (x)"}]}
    assert cf_member_set(cf) == frozenset({("8394", 1, "M02")})
    assert cf_member_set({}) == frozenset()


def test_all_outputs_registry_sourced():
    assert all_outputs_registry_sourced([]) is False  # empty -> not residue
    assert all_outputs_registry_sourced(
        [{"record_source": "pc_link_registry"}, {"record_source": "pc_logic_registry"}]
    ) is True
    # one output from a real output-module table -> not all registry-sourced
    assert all_outputs_registry_sourced(
        [{"record_source": "pc_link_registry"}, {"record_source": "output_module_table"}]
    ) is False
    # missing field (pre-0.5.22) -> source-unknown -> not all registry
    assert all_outputs_registry_sourced([{"channel": 1}]) is False


def test_has_pc_logic_module():
    assert has_pc_logic_module(None) is False
    assert has_pc_logic_module(
        {"nikobus_module": {"8110": {"module_type": "switch_module"}}}
    ) is False
    assert has_pc_logic_module(
        {"nikobus_module": {"940C": {"module_type": "pc_logic"}}}
    ) is True


def test_collect_button_outputs_flattens_op_points():
    phys = {
        "operation_points": {
            "1A": {"linked_modules": [
                {"module_address": "AABB", "outputs": [{"channel": 1}, {"channel": 2}]},
            ]},
            "1B": {"linked_modules": [
                {"module_address": "CCDD", "outputs": [{"channel": 3}]},
                "garbage",
            ]},
        }
    }
    assert collect_button_outputs(phys) == [
        {"channel": 1}, {"channel": 2}, {"channel": 3}
    ]
    assert collect_button_outputs({}) == []


def test_build_controlled_by_index():
    button_data = {
        "nikobus_button": {
            "1843B4": {
                "operation_points": {
                    "1A": {
                        "bus_address": "004E2C",
                        "description": "Living switch",
                        "linked_modules": [
                            {"module_address": "0e6c", "outputs": [
                                {"channel": 2, "mode": "M01"},
                            ]},
                        ],
                    }
                }
            }
        }
    }
    index = build_controlled_by_index(button_data)
    assert list(index.keys()) == [("0E6C", 2)]
    entry = index[("0E6C", 2)]
    assert len(entry) == 1
    assert entry[0]["bus_address"] == "004E2C"
    assert entry[0]["wall_button_address"] == "1843B4"
    assert entry[0]["wall_button_key"] == "1A"
    assert entry[0]["mode"] == "M01"


def test_build_controlled_by_index_empty_and_malformed():
    assert build_controlled_by_index(None) == {}
    assert build_controlled_by_index({"nikobus_button": "not-a-dict"}) == {}
