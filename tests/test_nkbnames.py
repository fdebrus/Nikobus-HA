"""Tests for .nkb parsing (names + rooms + scenes) and the registry apply."""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.nikobus.nkbnames import (
    CANONICAL_NKB_FILENAME,
    NkbData,
    SceneDef,
    _fmt_addr,
    _mode_code,
    find_nkb_file,
    parse_nkb,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_fmt_addr_module_is_4hex_button_is_6hex():
    assert _fmt_addr(3692) == "0E6C"      # 0x0E6C, 16-bit module
    assert _fmt_addr(37900) == "940C"
    assert _fmt_addr(1590196) == "1843B4"  # 24-bit button
    assert _fmt_addr(859264) == "0D1C80"


def test_mode_code_extracts_leading_m():
    assert _mode_code("M12 (Preset on)") == "M12"
    assert _mode_code("M04") == "M04"
    assert _mode_code("MCF") is None
    assert _mode_code(None) is None


# --------------------------------------------------------------------------- #
# find_nkb_file
# --------------------------------------------------------------------------- #
def test_find_canonical_preferred(tmp_path):
    (tmp_path / CANONICAL_NKB_FILENAME).write_bytes(b"x")
    (tmp_path / "other.nkb").write_bytes(b"x")
    assert find_nkb_file(str(tmp_path)).name == CANONICAL_NKB_FILENAME


def test_find_single_nkb(tmp_path):
    (tmp_path / "waterloo.nkb").write_bytes(b"x")
    assert find_nkb_file(str(tmp_path)).name == "waterloo.nkb"


def test_find_none_when_absent(tmp_path):
    assert find_nkb_file(str(tmp_path)) is None


def test_find_none_when_ambiguous(tmp_path):
    (tmp_path / "a.nkb").write_bytes(b"x")
    (tmp_path / "b.nkb").write_bytes(b"x")
    assert find_nkb_file(str(tmp_path)) is None


# --------------------------------------------------------------------------- #
# parse_nkb — addresses, rooms, scene member sets (parser stubbed)
# --------------------------------------------------------------------------- #
class _FakeParser:
    """Stands in for the vendored AccessParser. A tiny but representative
    install: a dimmer (0E6C/Centrale), a button (1843B4/Living), and a
    'Scene - Test' group whose trigger drives dimmer ch1 in M12."""

    _TABLES = {
        "Component": {
            "KeyComponent": [1, 2, 3, 4],
            "KeyLocation": [10, 11, 99, 10],
            "PhysicalAddress": [3692, 1590196, -1, 0],  # 0E6C, 1843B4, scene, skip
            "StrUserName": ["Dimcontroller", "Entree", "Scene - Test", ""],
        },
        "Location": {
            "KeyLocation": [10, 11, 99],
            "StrUserName": ["Centrale", "Living", "S_DB_GROUPS"],
        },
        "Objecten": {
            "KeyObject": [100, 200, 300],
            "KeyComponent": [1, 2, 3],     # output, trigger-input, group
            "KeyObjectBase": [500, 600, 700],
            "ObjectAddress": [0, 0, 0],
            "PhysicalObjectAddress": [0, 0, 0],
            "StrUserName": [None, "Input", None],
        },
        "ObjectBase": {
            "KeyObjectBase": [500, 600, 700],
            "ObjectAddress": [0, 0, 0],    # ch1 for the output
            "Prefix": ["O01", "1A", "CF"],
            "StrDescription": [None, None, None],
        },
        "LinkModeBase": {
            "KeyLinkMode": [10, 13],
            "StrMode": ["M12", "MCF"],
        },
        "Connection": {
            "KeyConnection": [1, 2],
            # MCF: group(300) -> trigger(200); member: output(100) <- trigger(200)
            "KeyObjectOut": [300, 100],
            "KeyObjectIn": [200, 200],
            "KeyLinkMode": [13, 10],
            "ParamValue1": [-1, 10],
        },
    }

    def __init__(self, _path):
        pass

    def parse_table(self, name):
        return self._TABLES[name]


def _make_nkb_zip(tmp_path):
    nkb = tmp_path / "p.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("__niko__.mdb", b"dummy")
    return nkb


def _parse(tmp_path):
    nkb = _make_nkb_zip(tmp_path)
    with patch(
        "custom_components.nikobus.vendor.access_parser.AccessParser", _FakeParser
    ):
        return parse_nkb(nkb)


def test_parse_addresses_with_rooms(tmp_path):
    data = _parse(tmp_path)
    assert isinstance(data, NkbData)
    assert data.addresses == {
        "0E6C": ("Dimcontroller", "Centrale"),
        "1843B4": ("Entree", "Living"),
    }


def test_parse_scene_member_set(tmp_path):
    data = _parse(tmp_path)
    assert len(data.scenes) == 1
    sc = data.scenes[0]
    assert sc.name == "Scene - Test"
    # trigger(200) drives output(100)=0E6C ch1 in M12; MCF link excluded
    assert sc.members == frozenset({("0E6C", 1, "M12")})


def test_parse_raises_without_mdb(tmp_path):
    nkb = tmp_path / "bad.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("notes.txt", b"no mdb")
    with pytest.raises(ValueError):
        parse_nkb(nkb)


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


def _entity(eid, device_id, name=None, original_name=None):
    e = MagicMock()
    e.entity_id = eid
    e.device_id = device_id
    e.name = name
    e.original_name = original_name
    return e


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


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
             patch("custom_components.nikobus.coordinator.dr.async_get",
                   return_value=dev_reg), \
             patch("custom_components.nikobus.coordinator.er.async_get",
                   return_value=ent_reg), \
             patch("custom_components.nikobus.coordinator.dr.async_entries_for_config_entry",
                   return_value=devices, create=True), \
             patch("custom_components.nikobus.coordinator.er.async_entries_for_config_entry",
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
    dev_cf = _device("d3", "DE4E2C", name="Nikobus scene DE4E2C")
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

    assert result == {"devices": 3, "entities": 2, "areas": 2,
                      "scenes": 1, "scenes_created": 0}
    names = {c.args[0]: c.kwargs.get("name")
             for c in dev_reg.async_update_device.call_args_list
             if "name" in c.kwargs}
    # name carries the room (disambiguates generic repeated names); scenes
    # (no room) keep their bare name.
    assert names == {"d1": "Dimcontroller (Centrale)", "d2": "Entree (Living)",
                     "d3": "Scene - Test"}
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
