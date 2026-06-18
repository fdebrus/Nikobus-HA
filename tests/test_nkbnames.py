"""Tests for the coordinator's ``.nkb`` apply: names, Areas, scene match.

The ``.nkb`` *parser* now lives in ``nikobus_connect.nkb`` and its unit
tests moved there (``test_nkb_parser.py``). This file keeps the
integration-side apply tests (``coordinator.async_import_nkb_names``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.nikobus.nkbnames import NkbData, SceneDef


# --------------------------------------------------------------------------- #
# coordinator.async_import_nkb_names — names, areas, scene match
# --------------------------------------------------------------------------- #
def _coord(cf=None, button_data=None):
    from custom_components.nikobus.coordinator import NikobusDataCoordinator

    c = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    c.hass = MagicMock()
    c.hass.config.config_dir = "/cfg"

    async def _aaej(fn, *a):
        return fn(*a)

    c.hass.async_add_executor_job = _aaej
    c.config_entry = MagicMock()
    c.config_entry.entry_id = "E1"
    c.dict_button_data = button_data or {}
    c.cf_storage = None
    if cf is not None:
        c.cf_storage = MagicMock()
        c.cf_storage.data = {"nikobus_cf": dict(cf)}

        async def _save():
            return None

        c.cf_storage.async_save = _save
    return c


def _opbtn(bus_address, *outputs):
    """Button-store physical with one op-point. outputs: (mod, ch, mode)."""
    by_mod = {}
    for mod, ch, mode in outputs:
        by_mod.setdefault(mod, []).append({"channel": ch, "mode": mode})
    return {
        "operation_points": {
            "K": {
                "bus_address": bus_address,
                "linked_modules": [
                    {"module_address": m, "outputs": o} for m, o in by_mod.items()
                ],
            }
        }
    }


def _device(dev_id, addr, name="old", area_id=None):
    d = MagicMock()
    d.id = dev_id
    d.identifiers = {("nikobus", addr)}
    d.name = name
    d.area_id = area_id
    return d


def _entity(eid, device_id, name=None, original_name=None, unique_id=None):
    e = MagicMock()
    e.entity_id = eid
    e.device_id = device_id
    e.name = name
    e.original_name = original_name
    e.unique_id = unique_id
    return e


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _patches(data, devices, entities, dev_reg, ent_reg, area_reg):
    import contextlib

    @contextlib.contextmanager
    def ctx():
        with patch("custom_components.nikobus.nkbnames.find_nkb_file",
                   return_value=Path("/cfg/nikobus.nkb")), \
             patch("custom_components.nikobus.nkbnames.parse_nkb",
                   return_value=data), \
             patch("homeassistant.helpers.area_registry.async_get",
                   return_value=area_reg, create=True), \
             patch("custom_components.nikobus.discovery_mixin.dr.async_get",
                   return_value=dev_reg), \
             patch("custom_components.nikobus.discovery_mixin.er.async_get",
                   return_value=ent_reg), \
             patch("custom_components.nikobus.discovery_mixin.dr.async_entries_for_config_entry",
                   return_value=devices, create=True), \
             patch("custom_components.nikobus.discovery_mixin.er.async_entries_for_config_entry",
                   return_value=entities, create=True):
            yield
    return ctx()


def test_import_names_areas_and_scene_match():
    data = NkbData(
        addresses={
            "0E6C": ("Dimcontroller", "Centrale"),
            "1843B4": ("Entree", "Living"),
        },
        scenes=[SceneDef("Scene - Test", frozenset({("0E6C", 1, "M12")}))],
    )
    # a CF whose members match the scene -> should be named
    coord = _coord(cf={"DE4E2C": {
        "outputs": [{"module_address": "0E6C", "channel": 1,
                     "mode": "M12 (Preset on)"}]}})

    dev_dim = _device("d1", "0E6C")               # module, 3 entities
    dev_btn = _device("d2", "1843B4")             # button, 1 entity
    # The scene lives on its own ``cf_<addr>`` device (split from any
    # trigger button), so the rename must reach the ``cf_`` identifier.
    dev_cf = _device("d3", "cf_de4e2c", name="Nikobus scene DE4E2C")
    devices = [dev_dim, dev_btn, dev_cf]
    entities = [
        _entity("light.a", "d1"), _entity("light.b", "d1"),
        _entity("binary_sensor.btn", "d2", original_name="Key A"),
        _entity("scene.de4e2c", "d3"),
    ]
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    area = MagicMock()
    area.id = "area_living"
    area_reg.async_get_area_by_name.return_value = None
    area_reg.async_create.return_value = area

    with _patches(data, devices, entities, dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names())

    assert result == {"devices": 3, "entities": 2, "channels": 0, "areas": 2,
                      "scenes": 1, "scenes_created": 0}
    names = {c.args[0]: c.kwargs.get("name")
             for c in dev_reg.async_update_device.call_args_list
             if "name" in c.kwargs}
    # name carries the room (disambiguates generic repeated names); scenes
    # (no room) keep their bare name.
    assert names == {"d1": "Dimcontroller (Centrale)", "d2": "Entree (Living)",
                     "d3": "Scene - Test"}
    # The matched name is persisted onto the CF record too — the scene
    # entity lives on its own ``cf_<addr>`` device (not merged into the
    # trigger button), which the address-keyed device rename can't reach,
    # so the name has to travel with the CF.
    assert coord.cf_storage.data["nikobus_cf"]["DE4E2C"]["name"] == "Scene - Test"
    # areas assigned for the two room-bearing devices (not the scene)
    area_calls = [c for c in dev_reg.async_update_device.call_args_list
                  if "area_id" in c.kwargs]
    assert {c.args[0] for c in area_calls} == {"d1", "d2"}


def test_import_creates_nkb_sourced_shutter_scene():
    """A named group with no matching CF (shutter scene — no light-scene
    mode) is created in cf_storage, keyed on the address that fires its
    member set, sourced 'nkb' with its real name."""
    data = NkbData(
        addresses={},
        scenes=[SceneDef("ShuttersSalonCuisine",
                         frozenset({("9105", 3, "M01"), ("9105", 5, "M01")}))],
    )
    # no CF for these members, but two op-points drive the exact set
    coord = _coord(cf={}, button_data={"nikobus_button": {
        "1843B4": _opbtn("AB1234", ("9105", 3, "M01 (Open - stop - close)"),
                         ("9105", 5, "M01 (Open - stop - close)")),
        "0D1C80": _opbtn("CD5678", ("9105", 3, "M01 (Open - stop - close)"),
                         ("9105", 5, "M01 (Open - stop - close)")),
    }})
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names())

    assert result["scenes_created"] == 1
    cf = coord.cf_storage.data["nikobus_cf"]
    # canonical = sorted-first of {AB1234, CD5678}
    assert "AB1234" in cf
    entry = cf["AB1234"]
    assert entry["source"] == "nkb"
    assert entry["name"] == "ShuttersSalonCuisine"
    assert entry["triggered_by"] == ["AB1234", "CD5678"]
    assert {(o["module_address"], o["channel"]) for o in entry["outputs"]} == \
        {("9105", 3), ("9105", 5)}
    # a reload is scheduled so the scene platform creates the entity
    coord.hass.async_create_task.assert_called_once()


def test_import_skips_triggerless_group():
    """A group whose member set no op-point drives (no on-bus trigger) is
    not created."""
    data = NkbData(addresses={},
                   scenes=[SceneDef("ShuttersUp",
                                    frozenset({("9105", 1, "M02")}))])
    coord = _coord(cf={}, button_data={"nikobus_button": {}})  # empty graph
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names())
    assert result["scenes_created"] == 0
    assert coord.cf_storage.data["nikobus_cf"] == {}


def test_ingest_preserves_nkb_sourced_scenes():
    """A re-discovery must not wipe nkb-sourced scenes."""
    from unittest.mock import AsyncMock

    coord = _coord(cf={
        "AB1234": {"bus_address": "AB1234", "pattern": "nkb_scene",
                   "outputs": [], "source": "nkb", "name": "ShuttersUp"},
    })
    # library re-classifies one discovered light scene
    cfb = MagicMock()
    cfb.bus_address = "DE4E2C"
    cfb.pattern = "light_scene"
    cfb.triggered_by = ["DE4E2C"]
    cfb.outputs = []
    coord.nikobus_discovery = MagicMock()
    coord.nikobus_discovery.discovered_cf_broadcasts = {"DE4E2C": cfb}
    coord.cf_storage.async_save = AsyncMock()

    _run(coord._ingest_cf_broadcasts())

    cf = coord.cf_storage.data["nikobus_cf"]
    assert "AB1234" in cf and cf["AB1234"]["source"] == "nkb"  # preserved
    assert "DE4E2C" in cf  # discovered, freshly ingested


def test_import_does_not_override_existing_area():
    data = NkbData(
        addresses={"1843B4": ("Entree", "Living")}, scenes=[]
    )
    coord = _coord()
    dev = _device("d2", "1843B4", area_id="already_set")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [dev], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names())
    assert result["areas"] == 0
    area_reg.async_create.assert_not_called()


def test_import_scene_no_match_when_members_differ():
    data = NkbData(
        addresses={}, scenes=[SceneDef("Scene - X", frozenset({("0E6C", 1, "M12")}))]
    )
    coord = _coord(cf={"AAAAAA": {
        "outputs": [{"module_address": "0E6C", "channel": 2,  # different channel
                     "mode": "M12 (Preset on)"}]}})
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [_device("d", "AAAAAA")], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names())
    assert result["scenes"] == 0


def test_import_raises_when_no_file():
    from homeassistant.exceptions import HomeAssistantError

    coord = _coord()
    with patch("custom_components.nikobus.nkbnames.find_nkb_file",
               return_value=None):
        with pytest.raises(HomeAssistantError):
            _run(coord.async_import_nkb_names())


# --------------------------------------------------------------------------- #
# channel names, category selection, overwrite
# --------------------------------------------------------------------------- #
def test_import_names_output_channels():
    """Per-output entities get the .nkb channel name (matched by unique_id);
    a placeholder/unset name is filled, an unmatched channel is left alone."""
    data = NkbData(
        addresses={}, scenes=[],
        outputs={("0E6C", 1): "Appliques Salon", ("9105", 2): "Terrasse"},
    )
    coord = _coord()
    entities = [
        _entity("light.a", "d1", unique_id="nikobus_light_module_0E6C_1"),
        _entity("cover.b", "d2", unique_id="nikobus_cover_module_9105_2"),
        _entity("switch.c", "d3", unique_id="nikobus_switch_module_AAAA_5"),
        _entity("binary_sensor.btn", "d4", unique_id="nikobus_binary_sensor_1843B4_1"),
    ]
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], entities, dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"channel_names"}))

    assert result["channels"] == 2
    renamed = {c.args[0]: c.kwargs.get("name")
               for c in ent_reg.async_update_entity.call_args_list}
    assert renamed == {"light.a": "Appliques Salon", "cover.b": "Terrasse"}


def test_import_category_selection_limits_work():
    """Selecting only ``device_names`` touches no areas and no scenes."""
    data = NkbData(
        addresses={"1843B4": ("Entree", "Living")}, scenes=[],
    )
    coord = _coord()
    dev = _device("d2", "1843B4")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [dev], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"device_names"}))

    assert result["devices"] == 1
    assert result["areas"] == 0
    area_reg.async_create.assert_not_called()
    area_calls = [c for c in dev_reg.async_update_device.call_args_list
                  if "area_id" in c.kwargs]
    assert area_calls == []


def test_import_overwrite_replaces_user_set_names():
    """Overwrite forces the device name onto ``name_by_user`` and the
    channel name onto an entity the user already renamed."""
    data = NkbData(
        addresses={"0E6C": ("Dimcontroller", "Centrale")}, scenes=[],
        outputs={("0E6C", 1): "Appliques Salon"},
    )
    coord = _coord()
    dev = _device("d1", "0E6C", name="old")
    dev.name_by_user = "MyOwnName"
    # two entities -> device-name logic leaves them to the channel loop
    chan = _entity("light.a", "d1", name="MyOwnLight",
                   unique_id="nikobus_light_module_0E6C_1")
    chan2 = _entity("light.b", "d1", unique_id="nikobus_light_module_0E6C_2")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [dev], [chan, chan2], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(
            categories={"device_names", "channel_names"}, overwrite=True))

    assert result["devices"] == 1
    assert result["channels"] == 1
    dev_reg.async_update_device.assert_any_call(
        "d1", name_by_user="Dimcontroller (Centrale)")
    ent_reg.async_update_entity.assert_called_once_with(
        "light.a", name="Appliques Salon")


def test_import_no_overwrite_keeps_user_set_channel_name():
    """Without overwrite, a channel the user already named is left alone."""
    data = NkbData(
        addresses={}, scenes=[], outputs={("0E6C", 1): "Appliques Salon"},
    )
    coord = _coord()
    chan = _entity("light.a", "d1", name="MyOwnLight",
                   unique_id="nikobus_light_module_0E6C_1")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [chan], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"channel_names"}))

    assert result["channels"] == 0
    ent_reg.async_update_entity.assert_not_called()
