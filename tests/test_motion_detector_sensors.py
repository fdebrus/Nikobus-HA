"""Occupancy and light sensors of the motion detector (05-7*5 / 430-00500).

Each unit sends its output on / off and its light level above / below
threshold as four button frames. Library 0.57.0 inventories all four;
the integration turns each pair into a stateful binary sensor.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import custom_components.nikobus.binary_sensor as bs
from custom_components.nikobus.const import press_signal
from custom_components.nikobus.router import (
    iter_motion_detectors,
    motion_sensor_unique_id,
)

#: Unit 0986BC as a 0.57.0 inventory stores it, and unit 098B48 as a store
#: written before (two keys, no device type) still holds it.
STORE = {
    "0986BC": {
        "type": "Motion detector with Nikobus interface",
        "model": "05-7*5",
        "device_type": "28",
        "channels": 4,
        "description": "Motion detector with Nikobus interface #N0986BC",
        "nkb_name": "Landing upstairs",
        "operation_points": {
            "1A": {"bus_address": "8F5864"},
            "1B": {"bus_address": "CF5864"},
            "1C": {"bus_address": "EF5864"},
            "1D": {"bus_address": "AF5864"},
        },
    },
    "098B48": {
        "type": "Motion detector with Nikobus interface",
        "model": "05-7*5",
        "channels": 2,
        "description": "Motion detector with Nikobus interface #N098B48",
        "operation_points": {
            "1A": {"bus_address": "84B464"},
            "1B": {"bus_address": "C4B464"},
        },
    },
    "0D1C80": {
        "type": "Bus push button, 4 control buttons",
        "model": "05-064",
        "channels": 4,
        "operation_points": {"1A": {"bus_address": "8E0383"}},
    },
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _setup(store=STORE, classes=()):
    coordinator = MagicMock()
    coordinator.dict_button_data = {"nikobus_button": store}
    coordinator.dict_module_data = {}
    entry = SimpleNamespace(runtime_data=coordinator, options={"button_classes": list(classes)}, entry_id="E1")
    added: list = []
    registered: list = []
    with patch.object(
        bs, "register_wall_button_devices",
        side_effect=lambda hass, entry_, buttons, modules, classes=None: registered.append((dict(buttons), classes)),
    ):
        _run(bs.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents)))
    sensors = [e for e in added if isinstance(e, bs.NikobusMotionDetectorSensor)]
    # The platform also registers the press entities' devices (filtered
    # by class); only the unfiltered detector registration is of interest.
    return sensors, [call for call in registered if call[1] is None]


def _by_id(sensors):
    return {s._attr_unique_id: s for s in sensors}


def test_each_detector_gets_an_occupancy_and_a_light_sensor():
    sensors, _ = _setup()
    by_id = _by_id(sensors)
    assert set(by_id) == {
        "nikobus_motion_0986bc_occupancy",
        "nikobus_motion_0986bc_light",
        "nikobus_motion_098b48_occupancy",
        "nikobus_motion_098b48_light",
    }
    occ = by_id["nikobus_motion_0986bc_occupancy"]
    assert (occ._on_address, occ._off_address) == ("8F5864", "CF5864")
    assert occ._attr_device_class == "occupancy"
    assert occ._attr_translation_key == "motion_occupancy"
    light = by_id["nikobus_motion_0986bc_light"]
    assert (light._on_address, light._off_address) == ("EF5864", "AF5864")
    assert light._attr_device_class == "light"


def test_a_store_from_before_still_gets_the_light_sensor_at_the_right_frames():
    sensors, _ = _setup()
    light = _by_id(sensors)["nikobus_motion_098b48_light"]
    assert (light._on_address, light._off_address) == ("E4B464", "A4B464")


def test_sensors_and_their_device_exist_with_no_press_class_selected():
    sensors, registered = _setup(classes=())
    assert len(sensors) == 4
    assert registered == [({k: STORE[k] for k in ("0986BC", "098B48")}, None)]


def test_sensors_hang_on_the_detector_device():
    sensors, _ = _setup()
    occ = _by_id(sensors)["nikobus_motion_0986bc_occupancy"]
    assert occ._attr_device_info["identifiers"] == {("nikobus", "0986BC")}
    assert occ._attr_device_info["name"] == "Landing upstairs"


def test_no_detector_no_sensor():
    sensors, registered = _setup(store={"0D1C80": STORE["0D1C80"]})
    assert sensors == [] and registered == []


def _sensor(kind="occupancy"):
    sensors, _ = _setup()
    sensor = _by_id(sensors)[motion_sensor_unique_id("0986BC", kind)]
    sensor.hass = MagicMock()
    sensor.async_write_ha_state = MagicMock()
    return sensor


def test_the_on_frame_sets_it_and_the_off_frame_clears_it():
    sensor = _sensor()
    assert sensor._attr_is_on is None  # unknown until the first frame
    sensor._handle_frame({"address": "8f5864"})
    assert sensor._attr_is_on is True
    sensor._handle_frame({"address": "CF5864"})
    assert sensor._attr_is_on is False
    assert sensor.async_write_ha_state.call_count == 2


def test_repeated_frames_write_the_state_once():
    sensor = _sensor("light")
    for _ in range(7):  # the interface repeats each telegram about seven times
        sensor._handle_frame({"address": "EF5864"})
    assert sensor._attr_is_on is True
    assert sensor.async_write_ha_state.call_count == 1


def test_another_address_changes_nothing():
    sensor = _sensor()
    sensor._handle_frame({"address": "EF5864"})
    assert sensor._attr_is_on is None
    sensor.async_write_ha_state.assert_not_called()


@pytest.mark.parametrize(("last", "expected"), [("on", True), ("off", False), ("unavailable", None)])
def test_the_last_state_is_restored_and_both_frames_are_listened_to(last, expected):
    sensor = _sensor()

    async def _last_state():
        return SimpleNamespace(state=last)

    sensor.async_get_last_state = _last_state
    with patch.object(bs, "async_dispatcher_connect", return_value=lambda: None) as connect:
        _run(sensor.async_added_to_hass())
    assert sensor._attr_is_on is expected
    signals = {c.args[1] for c in connect.call_args_list}
    assert {press_signal("8F5864"), press_signal("CF5864")} <= signals


def test_the_orphan_cleanup_knows_the_sensors_whatever_the_classes():
    from custom_components.nikobus.coordinator import NikobusDataCoordinator

    coordinator = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    coordinator.config_entry = SimpleNamespace(options={"button_classes": []})
    coordinator.dict_module_data = {}
    coordinator.dict_button_data = {"nikobus_button": STORE}
    coordinator.dict_scene_data = {"scene": []}
    coordinator.cf_storage = None
    known = coordinator.get_known_entity_unique_ids()
    assert {
        "nikobus_motion_0986bc_occupancy",
        "nikobus_motion_0986bc_light",
        "nikobus_motion_098b48_occupancy",
        "nikobus_motion_098b48_light",
    } <= known


def test_the_enumerator_finds_detectors_by_type_byte_model_or_name():
    found = dict(iter_motion_detectors(STORE))
    assert set(found) == {"0986BC", "098B48"}
