"""Unit tests for the pure reconciliation helpers (nkbreconcile)."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.nikobus.nkbreconcile import (
    all_outputs_registry_sourced,
    build_controlled_by_index,
    build_routing_graph,
    cf_member_set,
    cf_roller_directions,
    classify_button_status,
    collect_button_outputs,
    flatten_cf_broadcasts,
    has_pc_logic_module,
    member_set_from_outputs,
)


def _button(linked: list[tuple[str, int, str | None]], **extra):
    """Build a minimal button record with one op-point driving ``linked``
    outputs — each ``(module_address, channel, record_source)``."""
    outputs = [
        {"channel": ch, "mode": "M01", "record_source": src}
        for _mod, ch, src in linked
    ]
    # Group outputs under their module address.
    by_mod: dict[str, list[dict]] = {}
    for (mod, ch, src), out in zip(linked, outputs):
        by_mod.setdefault(mod, []).append(out)
    return {
        **extra,
        "operation_points": {
            "1A": {
                "bus_address": "004E2C",
                "linked_modules": [
                    {"module_address": mod, "outputs": outs}
                    for mod, outs in by_mod.items()
                ],
            }
        },
    }


def test_classify_button_status_synthesized_input():
    assert classify_button_status(
        {"pc_logic_parent_address": "940C"}, set(), False
    ) == "synthesized_input"


def test_classify_button_status_input_only():
    assert classify_button_status(
        {"type": "Universal interface, switch mode"}, set(), False
    ) == "input_only"


def test_classify_button_status_legacy_undecoded_when_no_outputs():
    assert classify_button_status({"operation_points": {}}, set(), False) == (
        "legacy_undecoded"
    )


def test_classify_button_status_active_when_linked_module_survives():
    phys = _button([("0E6C", 1, "output_module_table")])
    assert classify_button_status(phys, {"0E6C"}, False) == "active"


def test_classify_button_status_legacy_orphan_when_module_evicted():
    phys = _button([("0E6C", 1, "output_module_table")])
    assert classify_button_status(phys, {"C9A5"}, False) == "legacy_orphan"


def test_classify_button_status_registry_residue_without_pc_logic():
    phys = _button([("0E6C", 1, "pc_link_registry")])
    # No PC-Logic → all-registry-sourced is residue, even if the module
    # still exists.
    assert classify_button_status(phys, {"0E6C"}, False) == "legacy_orphan"
    # With PC-Logic present, defer to reachability (module survives → active).
    assert classify_button_status(phys, {"0E6C"}, True) == "active"


def test_flatten_cf_broadcasts():
    cf = SimpleNamespace(
        bus_address="384101",
        pattern="switch_pair",
        triggered_by=["384101", "004e2c"],
        outputs=[SimpleNamespace(module_address="0e6c", channel=2, mode="M02", t1=None, t2=None)],
    )
    flat = flatten_cf_broadcasts({"384101": cf})
    assert flat == {
        "384101": {
            "bus_address": "384101".upper(),
            "pattern": "switch_pair",
            "outputs": [
                {"module_address": "0E6C", "channel": 2, "mode": "M02", "t1": None, "t2": None}
            ],
            "triggered_by": ["384101", "004E2C"],
        }
    }


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


def test_build_routing_graph_groups_triggers_by_member_set():
    button_data = {
        "nikobus_button": {
            "AAAA": {"operation_points": {
                "1A": {"bus_address": "004e2c", "linked_modules": [
                    {"module_address": "0e6c", "outputs": [
                        {"channel": 1, "mode": "M01"},
                        {"channel": 1, "mode": "M01"},  # dupe -> deduped
                    ]},
                ]},
            }},
            # A second trigger driving the *same* member set -> grouped.
            "BBBB": {"operation_points": {
                "1A": {"bus_address": "1843B4", "linked_modules": [
                    {"module_address": "0E6C", "outputs": [{"channel": 1, "mode": "M01"}]},
                ]},
            }},
            # An op-point with no decodable members -> skipped.
            "CCCC": {"operation_points": {
                "1A": {"bus_address": "C0FFEE", "linked_modules": []},
            }},
        }
    }
    graph = build_routing_graph(button_data)
    key = frozenset({("0E6C", 1, "M01")})
    assert list(graph.keys()) == [key]
    addrs, outputs = graph[key]
    assert addrs == ["004E2C", "1843B4"]  # sorted, both triggers
    assert outputs == [{"module_address": "0E6C", "channel": 1, "mode": "M01",
                        "t1": None, "t2": None}]


def test_build_routing_graph_empty_and_malformed():
    assert build_routing_graph(None) == {}
    assert build_routing_graph({"nikobus_button": "nope"}) == {}


# ---------------------------------------------------------------------------
# cf_roller_directions — split a roller CF's outputs into open/close members
# ---------------------------------------------------------------------------
def test_cf_roller_directions_bidirectional_splits_open_and_close():
    """A 2-button roller CF lists each channel as M02 (open) + M03 (close);
    cf_roller_directions returns both directions with their members."""
    cf = {
        "pattern": "roller_pair",
        "outputs": [
            {"module_address": "8cf5", "channel": 1, "mode": "M02 (Open)", "t1": "40 s"},
            {"module_address": "8cf5", "channel": 2, "mode": "M02 (Open)", "t1": "30 s"},
            {"module_address": "8cf5", "channel": 1, "mode": "M03 (Close)", "t1": "40 s"},
            {"module_address": "8cf5", "channel": 2, "mode": "M03 (Close)", "t1": "30 s"},
        ],
    }
    dirs = cf_roller_directions(cf)
    assert set(dirs) == {"open", "close"}
    assert dirs["open"] == [
        {"module_address": "8CF5", "channel": 1, "time": "40 s"},
        {"module_address": "8CF5", "channel": 2, "time": "30 s"},
    ]
    assert dirs["close"] == [
        {"module_address": "8CF5", "channel": 1, "time": "40 s"},
        {"module_address": "8CF5", "channel": 2, "time": "30 s"},
    ]


def test_cf_roller_directions_preserves_first_sighting_order_per_direction():
    cf = {
        "outputs": [
            {"module_address": "B", "channel": 3, "mode": "M02", "t1": None},
            {"module_address": "A", "channel": 1, "mode": "M02", "t1": None},
            {"module_address": "B", "channel": 3, "mode": "M02", "t1": None},  # dupe
        ],
    }
    dirs = cf_roller_directions(cf)
    assert list(dirs) == ["open"]
    assert [(m["module_address"], m["channel"]) for m in dirs["open"]] == [
        ("B", 3), ("A", 1)
    ]


def test_cf_roller_directions_single_direction():
    """Close-only / open-only CFs yield just that one direction."""
    close_only = {"outputs": [
        {"module_address": "8CF5", "channel": 1, "mode": "M03 (Close)", "t1": "40 s"},
        {"module_address": "8CF5", "channel": 2, "mode": "M03 (Close)", "t1": "30 s"},
    ]}
    dirs = cf_roller_directions(close_only)
    assert list(dirs) == ["close"]
    assert len(dirs["close"]) == 2


def test_cf_roller_directions_m01_toggle_is_not_directional():
    """1-button M01 (open-stop-close) roller CFs have no M02/M03 → {}."""
    cf = {"outputs": [
        {"module_address": "C7C1", "channel": 1, "mode": "M01 (Open-stop-close)"},
    ]}
    assert cf_roller_directions(cf) == {}


def test_cf_roller_directions_drops_malformed():
    cf = {"outputs": [
        {"module_address": "8CF5", "channel": 2, "mode": "M02", "t1": "20 s"},
        "garbage",
        {"channel": 9, "mode": "M02"},          # no module
        {"module_address": "8CF5", "mode": "M03"},  # no channel
    ]}
    assert cf_roller_directions(cf) == {
        "open": [{"module_address": "8CF5", "channel": 2, "time": "20 s"}]
    }


def test_cf_roller_directions_empty():
    assert cf_roller_directions({}) == {}
    assert cf_roller_directions({"outputs": None}) == {}
