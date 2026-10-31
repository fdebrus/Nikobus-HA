"""Press entities follow a two-button plate's keys to their corrected address.

Library 0.57.1 moved the 05-060 / 05-061 / 05-060-02 keys from the generic
8 / C layout to 0 / 4 (nikobus-connect #184). Press entities are keyed by
bus address; without re-keying, the next inventory replaced them and the
orphan cleanup dropped the old ones with their names and areas.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.nikobus import _rekey_moved_plate_keys

PLATE = "1772B8"


def _store(op_1a="0753BA", op_1b="4753BA", **extra):
    return {
        PLATE: {
            "type": "Bus push button, 2 control buttons",
            "model": "05-060",
            "channels": 2,
            "operation_points": {"1A": {"bus_address": op_1a}, "1B": {"bus_address": op_1b}},
            **extra,
        }
    }


def _registry(existing: dict[tuple[str, str], str]):
    reg = MagicMock()
    reg.async_get_entity_id = lambda domain, platform, unique_id: existing.get((domain, unique_id))
    return reg


def _run(store, existing):
    reg = _registry(existing)
    with patch("custom_components.nikobus.er.async_get", return_value=reg):
        moved = _rekey_moved_plate_keys(MagicMock(), MagicMock(), store)
    return moved, [c.args + tuple(sorted(c.kwargs.items())) for c in reg.async_update_entity.call_args_list]


def test_the_old_entities_move_to_the_new_addresses():
    existing = {
        ("binary_sensor", "nikobus_button_8753BA"): "binary_sensor.hall_upper",
        ("button", "nikobus_push_button_8753BA"): "button.hall_upper",
        ("binary_sensor", "nikobus_button_C753BA"): "binary_sensor.hall_lower",
    }
    moved, calls = _run(_store(), existing)
    assert moved == 3
    assert sorted(calls) == sorted([
        ("binary_sensor.hall_upper", ("new_unique_id", "nikobus_button_0753BA")),
        ("button.hall_upper", ("new_unique_id", "nikobus_push_button_0753BA")),
        ("binary_sensor.hall_lower", ("new_unique_id", "nikobus_button_4753BA")),
    ])


def test_nothing_moves_before_an_inventory_has_moved_the_keys():
    existing = {("binary_sensor", "nikobus_button_8753BA"): "binary_sensor.hall_upper"}
    moved, calls = _run(_store("8753BA", "C753BA"), existing)
    assert (moved, calls) == (0, [])


def test_a_taken_new_id_is_left_alone():
    existing = {
        ("binary_sensor", "nikobus_button_8753BA"): "binary_sensor.old",
        ("binary_sensor", "nikobus_button_0753BA"): "binary_sensor.new",
    }
    moved, calls = _run(_store(), existing)
    assert (moved, calls) == (0, [])


def test_an_address_another_key_now_has_is_never_touched():
    store = _store()
    store["0A0908"] = {"model": "05-056", "channels": 2,
                       "operation_points": {"1A": {"bus_address": "8753BA"}}}
    existing = {("binary_sensor", "nikobus_button_8753BA"): "binary_sensor.interface_key"}
    moved, calls = _run(store, existing)
    assert (moved, calls) == (0, [])


def test_plates_with_the_generic_layout_are_not_touched():
    store = {"0A0908": {"model": "05-056", "channels": 2,
                        "operation_points": {"1A": {"bus_address": "0000AA"}}}}
    existing = {("binary_sensor", "nikobus_button_8000AA"): "binary_sensor.x"}
    assert _run(store, existing) == (0, [])


def test_the_type_byte_is_enough_without_a_model():
    store = _store(device_type="05")
    store[PLATE]["model"] = ""
    existing = {("button", "nikobus_push_button_C753BA"): "button.lower"}
    moved, calls = _run(store, existing)
    assert moved == 1
    assert calls == [("button.lower", ("new_unique_id", "nikobus_push_button_4753BA"))]
