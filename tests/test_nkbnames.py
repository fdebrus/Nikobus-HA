"""Tests for .nkb name extraction and the registry-apply import."""

from __future__ import annotations

import asyncio
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from custom_components.nikobus.nkbnames import (
    CANONICAL_NKB_FILENAME,
    NkbNames,
    find_nkb_file,
    parse_nkb_names,
)


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
# parse_nkb_names — transformation logic, parser stubbed
# --------------------------------------------------------------------------- #
class _FakeParser:
    """Stands in for the vendored AccessParser, column->list shape."""

    _TABLES = {
        "Component": {
            "KeyComponent": [1, 2, 3, 4],
            "KeyLocation": [10, 11, 99, 10],
            "PhysicalAddress": [3692, 859264, -1, 0],  # 0E6C, 0D1C80, scene, skip
            "StrUserName": ["Dimcontroller", "Canape", "Scene - Dinner", ""],
        },
        "Location": {
            "KeyLocation": [10, 11, 99],
            "StrUserName": ["Centrale", "Living", "S_DB_GROUPS"],
        },
    }

    def __init__(self, _path):
        pass

    def parse_table(self, name):
        return self._TABLES[name]


def _make_nkb_zip(tmp_path):
    nkb = tmp_path / "p.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("__niko__.mdb", b"dummy-bytes")
    return nkb


def test_parse_maps_addresses_rooms_and_scenes(tmp_path):
    nkb = _make_nkb_zip(tmp_path)
    with patch(
        "custom_components.nikobus.vendor.access_parser.AccessParser", _FakeParser
    ):
        result = parse_nkb_names(nkb)
    assert isinstance(result, NkbNames)
    # PhysicalAddress decimal -> 24-bit hex, with room label
    assert result.addresses == {
        "000E6C": "Dimcontroller (Centrale)",
        "0D1C80": "Canape (Living)",
    }
    # PhysicalAddress == -1 -> scene name, no room (group sentinel stripped)
    assert result.scenes == {"Scene - Dinner": ""}


def test_parse_raises_without_mdb(tmp_path):
    nkb = tmp_path / "bad.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("notes.txt", b"no mdb here")
    with pytest.raises(ValueError):
        parse_nkb_names(nkb)


# --------------------------------------------------------------------------- #
# coordinator.async_import_nkb_names — registry apply + non-clobber guards
# --------------------------------------------------------------------------- #
def _coord():
    from custom_components.nikobus.coordinator import NikobusDataCoordinator

    c = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    c.hass = MagicMock()
    c.hass.config.config_dir = "/cfg"

    async def _aaej(fn, *a):
        return fn(*a)

    c.hass.async_add_executor_job = _aaej
    c.config_entry = MagicMock()
    c.config_entry.entry_id = "E1"
    return c


def _device(dev_id, addr, name="old", name_by_user=None):
    d = MagicMock()
    d.id = dev_id
    d.identifiers = {("nikobus", addr)}
    d.name = name
    d.name_by_user = name_by_user
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


def test_import_names_devices_and_single_entity():
    c = _coord()
    names = NkbNames(
        addresses={"0E6C": "Dimcontroller (Centrale)", "1843B4": "Entree (Living)"},
        scenes={"Scene - TV": ""},
    )
    # dimmer device: 3 entities -> device renamed, entities inherit (not renamed)
    # button device: 1 entity, no user name -> both renamed
    dev_dim = _device("d1", "0E6C")
    dev_btn = _device("d2", "1843B4")
    dev_unmatched = _device("d3", "FFFFFF")
    devices = [dev_dim, dev_btn, dev_unmatched]
    entities = [
        _entity("light.a", "d1"),
        _entity("light.b", "d1"),
        _entity("light.c", "d1"),
        _entity("binary_sensor.btn", "d2", name=None, original_name="Key A"),
    ]
    dev_reg, ent_reg = MagicMock(), MagicMock()

    with patch("custom_components.nikobus.nkbnames.find_nkb_file",
               return_value=__import__("pathlib").Path("/cfg/nikobus.nkb")), \
         patch("custom_components.nikobus.nkbnames.parse_nkb_names",
               return_value=names), \
         patch("custom_components.nikobus.coordinator.dr.async_get",
               return_value=dev_reg), \
         patch("custom_components.nikobus.coordinator.er.async_get",
               return_value=ent_reg), \
         patch("custom_components.nikobus.coordinator.dr.async_entries_for_config_entry",
               return_value=devices, create=True), \
         patch("custom_components.nikobus.coordinator.er.async_entries_for_config_entry",
               return_value=entities, create=True):
        result = _run(c.async_import_nkb_names())

    assert result == {"devices": 2, "entities": 1, "addresses": 2, "scenes": 1}
    # both matched devices renamed
    renamed = {ca.args[0]: ca.kwargs["name"]
               for ca in dev_reg.async_update_device.call_args_list}
    assert renamed == {"d1": "Dimcontroller (Centrale)", "d2": "Entree (Living)"}
    # only the single-entity device's entity renamed
    ent_reg.async_update_entity.assert_called_once_with(
        "binary_sensor.btn", name="Entree (Living)"
    )


def test_import_preserves_user_named_entity():
    c = _coord()
    names = NkbNames(addresses={"1843B4": "Entree (Living)"}, scenes={})
    dev_btn = _device("d2", "1843B4")
    # entity already user-named -> must NOT be touched
    ent = _entity("binary_sensor.btn", "d2", name="My Custom Name")
    dev_reg, ent_reg = MagicMock(), MagicMock()
    with patch("custom_components.nikobus.nkbnames.find_nkb_file",
               return_value=__import__("pathlib").Path("/cfg/x.nkb")), \
         patch("custom_components.nikobus.nkbnames.parse_nkb_names",
               return_value=names), \
         patch("custom_components.nikobus.coordinator.dr.async_get",
               return_value=dev_reg), \
         patch("custom_components.nikobus.coordinator.er.async_get",
               return_value=ent_reg), \
         patch("custom_components.nikobus.coordinator.dr.async_entries_for_config_entry",
               return_value=[dev_btn], create=True), \
         patch("custom_components.nikobus.coordinator.er.async_entries_for_config_entry",
               return_value=[ent], create=True):
        result = _run(c.async_import_nkb_names())
    assert result["entities"] == 0
    ent_reg.async_update_entity.assert_not_called()


def test_import_raises_when_no_file():
    from homeassistant.exceptions import HomeAssistantError

    c = _coord()
    with patch("custom_components.nikobus.nkbnames.find_nkb_file",
               return_value=None):
        with pytest.raises(HomeAssistantError):
            _run(c.async_import_nkb_names())
