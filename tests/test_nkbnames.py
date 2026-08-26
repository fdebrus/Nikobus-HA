"""Tests for the coordinator's ``.nkb`` apply: names, Areas, scene match.

The ``.nkb`` *parser* now lives in ``nikobus_connect.nkb`` and its unit
tests moved there (``test_nkb_parser.py``). This file keeps the
integration-side apply tests (``coordinator.async_import_nkb_names``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nikobus.nkbnames import NkbData, SceneDef


# --------------------------------------------------------------------------- #
# coordinator.async_import_nkb_names — names, areas, scene match
# --------------------------------------------------------------------------- #
def _coord(cf=None, button_data=None, modules=None):
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
    c.dict_module_data = {}
    c.button_storage = MagicMock()
    c.button_storage.async_save = AsyncMock()
    c.cf_storage = None
    if cf is not None:
        c.cf_storage = MagicMock()
        c.cf_storage.data = {"nikobus_cf": dict(cf)}

        async def _save():
            return None

        c.cf_storage.async_save = _save

    c.module_storage = None
    if modules is not None:
        c.module_storage = MagicMock()
        c.module_storage.data = {"nikobus_module": modules}
        c.module_storage.async_save = AsyncMock()
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


def _device(dev_id, addr, name="old", area_id=None, via_device_id=None):
    d = MagicMock()
    d.id = dev_id
    d.identifiers = {("nikobus", addr)}
    d.name = name
    d.area_id = area_id
    d.via_device_id = via_device_id
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

    assert result == {"devices": 3, "keys": 0, "entities": 2, "channels": 0,
                      "outputs_enabled": 0, "areas": 2, "scenes": 1}
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


def test_import_does_not_surface_button_fired_group_as_scene():
    """A named group fired by real buttons on the bus is NOT turned into a
    scene: that would duplicate a button the user already has. The buttons
    are left untouched and no CF is created."""
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

    assert result["scenes"] == 0
    # nothing created, the buttons are left as-is
    assert coord.cf_storage.data["nikobus_cf"] == {}


def test_import_names_existing_discovered_cf_scene():
    """A named group that matches an already-discovered CF broadcast (no
    physical button — a 3880/3841 PC-Logic address) is NAMED from the .nkb,
    not recreated. The name is persisted onto the CF record."""
    data = NkbData(
        addresses={},
        scenes=[SceneDef("CloseHouse",
                         frozenset({("9105", 1, "M03")}))],
    )
    coord = _coord(cf={"3880AA": {
        "bus_address": "3880AA", "pattern": "roller_pair",
        "outputs": [{"module_address": "9105", "channel": 1,
                     "mode": "M03 (Close)"}]}})
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names())

    assert result["scenes"] == 1
    assert coord.cf_storage.data["nikobus_cf"]["3880AA"]["name"] == "CloseHouse"


def test_import_purges_stale_nkb_sourced_scene():
    """Migration: a button-duplicating scene a previous import created
    (source='nkb') is removed on the next import, and a reload is scheduled
    so the now-dead scene entity is torn down."""
    data = NkbData(addresses={}, scenes=[])
    coord = _coord(cf={
        "AB1234": {"bus_address": "AB1234", "pattern": "nkb_scene",
                   "outputs": [], "source": "nkb", "name": "ShuttersUp"},
    })
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names())

    assert coord.cf_storage.data["nikobus_cf"] == {}
    coord.hass.async_create_task.assert_called_once()


def test_ingest_drops_stale_nkb_sourced_scenes():
    """A re-discovery clears stale nkb-sourced scenes (per-button duplicates
    we no longer create), keeping only freshly discovered broadcasts."""
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
    assert "AB1234" not in cf  # stale nkb-sourced scene dropped
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


# --------------------------------------------------------------------------- #
# async_import_nkb_names — enabling previously-hidden output channels
#
# The register scan never learns channel *names* (Nikobus modules don't
# store per-channel text on the bus), so every channel starts as the
# "not_in_use output_N" placeholder router.py checks to skip entity
# creation. Before this fix, the .nkb import could only rename an entity
# that already existed — a channel with none stayed hidden forever, with
# no way out short of the manual "Customize a module" flow. These pin the
# fix: the import now writes the .nkb's real output name straight into
# module storage so router.py creates the entity on the next reload.
# --------------------------------------------------------------------------- #
def test_import_enables_previously_hidden_output_channel():
    data = NkbData(
        addresses={}, scenes=[], outputs={("0E6C", 4): "Appliques Salon"},
    )
    modules = {"0E6C": {
        "module_type": "switch_module",
        "description": "Switch S1",
        "channels": [
            {"description": "not_in_use output_1"},
            {"description": "not_in_use output_2"},
            {"description": "not_in_use output_3"},
            {"description": "not_in_use output_4"},
        ],
    }}
    coord = _coord(modules=modules)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"channel_names"}))

    assert result["outputs_enabled"] == 1
    assert modules["0E6C"]["channels"][3]["description"] == "Appliques Salon"
    # untouched siblings stay hidden
    assert modules["0E6C"]["channels"][0]["description"] == "not_in_use output_1"
    coord.module_storage.async_save.assert_awaited_once()
    # the entity set changed -> a reload is scheduled so it's created
    coord.hass.async_create_task.assert_called_once()


def test_import_does_not_touch_already_enabled_channel():
    """A channel that already has a real description (an entity already
    exists for it) is left to the rename loop — this path only unlocks
    channels that are still ``not_in_use``."""
    data = NkbData(
        addresses={}, scenes=[], outputs={("0E6C", 1): "Appliques Salon"},
    )
    modules = {"0E6C": {
        "module_type": "switch_module",
        "channels": [{"description": "Already Named"}],
    }}
    coord = _coord(modules=modules)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"channel_names"}))

    assert result["outputs_enabled"] == 0
    assert modules["0E6C"]["channels"][0]["description"] == "Already Named"
    coord.module_storage.async_save.assert_not_awaited()
    coord.hass.async_create_task.assert_not_called()


def test_import_does_not_enable_explicitly_disabled_channel():
    """A channel the user hid via "Customize a module" (entity_type =
    'disabled') must stay hidden even though the .nkb has a name for it."""
    data = NkbData(
        addresses={}, scenes=[], outputs={("0E6C", 1): "Appliques Salon"},
    )
    modules = {"0E6C": {
        "module_type": "switch_module",
        "channels": [{"description": "not_in_use output_1",
                      "entity_type": "disabled"}],
    }}
    coord = _coord(modules=modules)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"channel_names"}))

    assert result["outputs_enabled"] == 0
    assert modules["0E6C"]["channels"][0]["description"] == "not_in_use output_1"
    coord.module_storage.async_save.assert_not_awaited()


def test_import_enable_skipped_when_channel_names_category_excluded():
    data = NkbData(
        addresses={}, scenes=[], outputs={("0E6C", 1): "Appliques Salon"},
    )
    modules = {"0E6C": {
        "module_type": "switch_module",
        "channels": [{"description": "not_in_use output_1"}],
    }}
    coord = _coord(modules=modules)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"device_names"}))

    assert result["outputs_enabled"] == 0
    assert modules["0E6C"]["channels"][0]["description"] == "not_in_use output_1"


def test_import_no_module_storage_is_safe():
    """No module_storage configured (shouldn't happen in practice, but the
    coordinator's cf_storage sibling is defensively None-checked the same
    way) — the import must not crash."""
    data = NkbData(
        addresses={}, scenes=[], outputs={("0E6C", 1): "Appliques Salon"},
    )
    coord = _coord()  # module_storage stays None
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"channel_names"}))

    assert result["outputs_enabled"] == 0


# --------------------------------------------------------------------------- #
# async_import_nkb_names — Niko-app index prefix, room fallback, key devices
#
# Feature request from a user who reverse-engineered their .nkb: the plate
# label shown in the Nikobus PC software is "BP<Number>: <StrUserName>", so
# import the index (locale-neutral, no BP prefix), fall back to the room for
# installer-unnamed plates, and give per-key child devices a "<plate> Key
# <label>" name instead of the generated "Push button 1A #N<addr>".
# --------------------------------------------------------------------------- #
def test_import_button_plate_gets_number_prefix():
    data = NkbData(
        addresses={"39D7F6": ("Porte buanderie", "Buanderie")},
        scenes=[],
        numbers={"39D7F6": 7},
    )
    coord = _coord()
    dev = _device("d1", "39D7F6")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [dev], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    dev_reg.async_update_device.assert_called_once_with(
        "d1", name="7: Porte buanderie (Buanderie)"
    )


def test_import_module_does_not_get_number_prefix():
    """Modules (4-hex addresses) keep their plain name — the app-style
    index is only applied to button plates (6-hex)."""
    data = NkbData(
        addresses={"0E6C": ("Dimcontroller", "Centrale")},
        scenes=[],
        numbers={"0E6C": 1},
    )
    coord = _coord()
    dev = _device("d1", "0E6C")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [dev], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    dev_reg.async_update_device.assert_called_once_with(
        "d1", name="Dimcontroller (Centrale)"
    )


def test_import_unnamed_plate_falls_back_to_room():
    """An installer-unnamed plate (name "" in the .nkb) is labelled with
    its room — shown bare, not the redundant 'Room (Room)'."""
    data = NkbData(
        addresses={"3C1A57": ("", "Chambre 2")},
        scenes=[],
        numbers={"3C1A57": 9},
    )
    coord = _coord()
    dev = _device("d1", "3C1A57")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [dev], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    dev_reg.async_update_device.assert_any_call("d1", name="9: Chambre 2")


def test_import_renames_key_child_devices():
    """A per-key child device with the generated 'Push button 1A #N…'
    name is renamed '<plate> Key 1A' once its parent plate is matched.
    A child whose name doesn't match the generated pattern (IR /
    PC-Logic input keys, user-meaningful names) is left alone."""
    data = NkbData(
        addresses={"39D7F6": ("Porte buanderie", "")},
        scenes=[],
    )
    coord = _coord()
    plate = _device("d1", "39D7F6")
    key_a = _device("d2", "9A43A2", name="Push button 1A #N9A43A2",
                    via_device_id="d1")
    key_b = _device("d3", "DA43A2", name="Push button 1B #NDA43A2",
                    via_device_id="d1")
    ir_key = _device("d4", "30A111", name="IR 30A on 0D1C80",
                     via_device_id="d1")
    orphan = _device("d5", "111111", name="Push button 1A #N111111",
                     via_device_id="other")  # parent not matched
    devices = [plate, key_a, key_b, ir_key, orphan]
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, devices, [], dev_reg, ent_reg, area_reg):
        result = _run(coord.async_import_nkb_names(categories={"device_names"}))

    assert result["keys"] == 2
    dev_reg.async_update_device.assert_any_call(
        "d2", name="Porte buanderie Key 1A"
    )
    dev_reg.async_update_device.assert_any_call(
        "d3", name="Porte buanderie Key 1B"
    )
    renamed = {c.args[0] for c in dev_reg.async_update_device.call_args_list}
    assert "d4" not in renamed  # IR key untouched
    assert "d5" not in renamed  # unmatched parent untouched


def test_import_key_rename_uses_plain_plate_name():
    """The key label uses the bare plate name — no index prefix, no room
    suffix — to keep 'Porte buanderie Key 1A' readable."""
    data = NkbData(
        addresses={"39D7F6": ("Porte buanderie", "Buanderie")},
        scenes=[],
        numbers={"39D7F6": 7},
    )
    coord = _coord()
    plate = _device("d1", "39D7F6")
    key_a = _device("d2", "9A43A2", name="Push button 2C #N9A43A2",
                    via_device_id="d1")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [plate, key_a], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    dev_reg.async_update_device.assert_any_call(
        "d2", name="Porte buanderie Key 2C"
    )


def test_import_key_rename_overwrite_sets_name_by_user():
    data = NkbData(addresses={"39D7F6": ("Porte buanderie", "")}, scenes=[])
    coord = _coord()
    plate = _device("d1", "39D7F6")
    plate.name_by_user = None
    key_a = _device("d2", "9A43A2", name="Push button 1A #N9A43A2",
                    via_device_id="d1")
    key_a.name_by_user = None
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [plate, key_a], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(
            categories={"device_names"}, overwrite=True))

    dev_reg.async_update_device.assert_any_call(
        "d2", name_by_user="Porte buanderie Key 1A"
    )


# --------------------------------------------------------------------------- #
# async_import_nkb_names — persistence into the integration's own stores
#
# 3.11.0 field report: a non-overwrite import wrote names only into the
# device registry's integration-owned ``name`` field, which every restart
# overwrote again from ``DeviceInfo(name=<generated>)`` — the imported
# names silently reverted. The import now also persists ``nkb_name`` on
# the stored button / op-point / module entries; the registration paths
# prefer it, so the import survives restarts by construction.
# --------------------------------------------------------------------------- #
def _plate_store_entry():
    return {
        "type": "Bus push button, 4 control buttons",
        "model": "05-064",
        "channels": 4,
        "description": "Bus push button, 4 control buttons #N39D7F6",
        "operation_points": {
            "1A": {
                "bus_address": "9A43A2",
                "description": "Push button 1A #N9A43A2",
            },
            "1B": {
                "bus_address": "DA43A2",
                "description": "Push button 1B #NDA43A2",
            },
            "IR:30A": {
                "bus_address": "30A111",
                "description": "IR code 30A #I30A",
            },
        },
    }


def test_import_persists_plate_and_key_names_into_button_store():
    data = NkbData(
        addresses={"39D7F6": ("Porte buanderie", "Buanderie")},
        scenes=[],
        numbers={"39D7F6": 7},
    )
    store = {"nikobus_button": {"39D7F6": _plate_store_entry()}}
    coord = _coord(button_data=store)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    phys = store["nikobus_button"]["39D7F6"]
    assert phys["nkb_name"] == "7: Porte buanderie (Buanderie)"
    # Wall keys get "<plain plate name> Key <label>" — no index, no room.
    assert phys["operation_points"]["1A"]["nkb_name"] == "Porte buanderie Key 1A"
    assert phys["operation_points"]["1B"]["nkb_name"] == "Porte buanderie Key 1B"
    # IR op-points never match the generated-name gate — left alone.
    assert "nkb_name" not in phys["operation_points"]["IR:30A"]
    coord.button_storage.async_save.assert_awaited()


def test_import_persists_module_name_into_module_store():
    data = NkbData(
        addresses={"0E6C": ("Dimcontroller", "Centrale")},
        scenes=[],
        numbers={"0E6C": 1},
    )
    modules = {"0E6C": {"description": "Dimmer module", "module_type": "dimmer_module"}}
    coord = _coord(modules=modules)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    # Modules take the plain "Name (Room)" display — no index prefix.
    assert modules["0E6C"]["nkb_name"] == "Dimcontroller (Centrale)"
    coord.module_storage.async_save.assert_awaited()
    # The derived grouped view is refreshed so the live routing data
    # carries the imported name too.
    assert (
        coord.dict_module_data["dimmer_module"]["0E6C"]["nkb_name"]
        == "Dimcontroller (Centrale)"
    )


def test_import_persists_in_overwrite_mode_too():
    """Overwrite mode survives restarts via ``name_by_user`` alone, but
    the stored default should still track the import so a later
    "reset to default name" in the HA UI lands on the .nkb name."""
    data = NkbData(addresses={"39D7F6": ("Porte buanderie", "")}, scenes=[])
    store = {"nikobus_button": {"39D7F6": _plate_store_entry()}}
    coord = _coord(button_data=store)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(
            categories={"device_names"}, overwrite=True))

    assert store["nikobus_button"]["39D7F6"]["nkb_name"] == "Porte buanderie"


def test_import_persistence_skipped_without_device_names_category():
    data = NkbData(addresses={"39D7F6": ("Porte buanderie", "")}, scenes=[])
    store = {"nikobus_button": {"39D7F6": _plate_store_entry()}}
    coord = _coord(button_data=store)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"areas"}))

    assert "nkb_name" not in store["nikobus_button"]["39D7F6"]
    coord.button_storage.async_save.assert_not_awaited()


def test_import_persistence_no_save_when_names_unchanged():
    """Re-running the same import must not rewrite storage."""
    data = NkbData(
        addresses={"39D7F6": ("Porte buanderie", "Buanderie")},
        scenes=[],
        numbers={"39D7F6": 7},
    )
    store = {"nikobus_button": {"39D7F6": _plate_store_entry()}}
    coord = _coord(button_data=store)
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))
        coord.button_storage.async_save.reset_mock()
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    coord.button_storage.async_save.assert_not_awaited()


def test_reimport_with_changed_plate_name_refreshes_key_devices():
    """A key device renamed by a PREVIOUS import no longer matches the
    'Push button …' gate in the registry pass — the persistence pass
    self-heals it when the plate's .nkb name changes, using the stored
    previous import name as proof the field is ours to update."""
    store = {"nikobus_button": {"39D7F6": _plate_store_entry()}}
    store["nikobus_button"]["39D7F6"]["operation_points"]["1A"][
        "nkb_name"
    ] = "Ancien nom Key 1A"
    data = NkbData(addresses={"39D7F6": ("Nouveau nom", "")}, scenes=[])
    coord = _coord(button_data=store)
    key_dev = _device("d2", "9A43A2", name="Ancien nom Key 1A")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    dev_reg.async_get_device.return_value = key_dev
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    ops = store["nikobus_button"]["39D7F6"]["operation_points"]
    assert ops["1A"]["nkb_name"] == "Nouveau nom Key 1A"
    dev_reg.async_update_device.assert_any_call("d2", name="Nouveau nom Key 1A")


def test_reimport_key_heal_leaves_foreign_registry_names_alone():
    """If the registry name is neither the generated pattern nor the
    previous import's name, the self-heal must not touch it."""
    store = {"nikobus_button": {"39D7F6": _plate_store_entry()}}
    store["nikobus_button"]["39D7F6"]["operation_points"]["1A"][
        "nkb_name"
    ] = "Ancien nom Key 1A"
    data = NkbData(addresses={"39D7F6": ("Nouveau nom", "")}, scenes=[])
    coord = _coord(button_data=store)
    key_dev = _device("d2", "9A43A2", name="Something else entirely")
    dev_reg, ent_reg, area_reg = MagicMock(), MagicMock(), MagicMock()
    dev_reg.async_get_device.return_value = key_dev
    with _patches(data, [], [], dev_reg, ent_reg, area_reg):
        _run(coord.async_import_nkb_names(categories={"device_names"}))

    # Storage still tracks the new name (restart applies it as default)…
    ops = store["nikobus_button"]["39D7F6"]["operation_points"]
    assert ops["1A"]["nkb_name"] == "Nouveau nom Key 1A"
    # …but the mismatched registry name is not rewritten.
    renamed = {
        c.args[0]
        for c in dev_reg.async_update_device.call_args_list
        if "name" in c.kwargs
    }
    assert "d2" not in renamed
