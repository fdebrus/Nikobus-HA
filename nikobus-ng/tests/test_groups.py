"""Devices filed by kind under the gateway, as the Nikobus integration files them."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.nikobus_ng import async_remove_config_entry_device
from custom_components.nikobus_ng.const import DOMAIN
from custom_components.nikobus_ng.entity import plate_group
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from nikobus_connect.discovery.mapping import DEVICE_TYPES
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import AUDIO, ENTRY_ID, KEY_SCENE, PLATE, SWITCH, SetupFixture
from .test_remaining import _reloaded


@pytest.mark.parametrize(
    ("entry", "group"),
    [
        pytest.param({"type": DEVICE_TYPES["04"]["Name"]}, "wall_buttons", id="wall-plate"),
        pytest.param({"type": DEVICE_TYPES["0C"]["Name"]}, "wall_buttons", id="plate-with-ir-receiver"),
        pytest.param({"type": DEVICE_TYPES["23"]["Name"]}, "remotes", id="rf-wall-transmitter"),
        pytest.param({"type": DEVICE_TYPES["25"]["Name"]}, "remotes", id="hand-held-transmitter"),
        pytest.param({"type": DEVICE_TYPES["44"]["Name"]}, "interfaces", id="universal-interface"),
        pytest.param({"type": DEVICE_TYPES["22"]["Name"]}, "interfaces", id="switch-interface"),
        pytest.param({"type": "Button", "description": "RF remote"}, "remotes", id="named-in-a-project-file"),
        pytest.param({"type": "Button", "pc_logic_parent_type": "pc_logic"}, "system_modules", id="logical-input"),
        pytest.param({}, "wall_buttons", id="nothing-known"),
    ],
)
def test_a_plate_is_filed_by_what_the_inventory_calls_it(entry: dict[str, Any], group: str) -> None:
    """The names are the library's own, so a renamed product is caught here."""
    assert plate_group(entry) == group


async def test_devices_hang_under_their_kind_and_no_kind_is_empty(
    hass: HomeAssistant, hass_storage: dict[str, Any], scanned: SetupFixture, entry: MockConfigEntry
) -> None:
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["names"] = {
        "scenes": [{"name": "Dinner", "members": [[SWITCH, 5, "M14"], [SWITCH, 9, "M14"]]}]
    }
    hub = await scanned()
    devices = dr.async_get(hass)
    filed = {
        device.name: devices.async_get(device.via_device_id).name
        for identifier in (SWITCH, PLATE, AUDIO, f"{KEY_SCENE}_scene")
        if (device := devices.async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id))
    }
    assert filed == {
        "Switch module 4707": "Output modules",
        "Bus push button, 4 control buttons #N1843B4": "Wall buttons",
        "Audio distribution 8334": "System modules",
        "Dinner": "Scenes",
    }
    groups = {
        device.name
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
        if device.via_device_id == hub.gateway_device_id
    }
    # The installation has no remote, no interface and no shutter group.
    assert groups == {"Output modules", "System modules", "Wall buttons", "Scenes"}


async def test_a_kind_nothing_is_filed_under_any_more_goes_and_none_can_be_deleted(
    hass: HomeAssistant, hass_storage: dict[str, Any], scanned: SetupFixture, entry: MockConfigEntry
) -> None:
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["names"] = {
        "scenes": [{"name": "Dinner", "members": [[SWITCH, 5, "M14"], [SWITCH, 9, "M14"]]}]
    }
    hub = await scanned()
    devices = dr.async_get(hass)
    scenes = devices.async_get(hub.group_device_id("scenes"))
    assert scenes is not None
    assert not await async_remove_config_entry_device(hass, entry, scenes)
    # The project no longer names the scene: it is a key again, and nothing is left under "Scenes".
    hub.inventory.names["scenes"] = []
    await hub.inventory.async_save()
    await hass.config_entries.async_reload(entry.entry_id)
    await _reloaded(hass, entry, hub)
    assert devices.async_get(scenes.id) is None
    assert devices.async_get_device_by_identifier((DOMAIN, f"{KEY_SCENE}_scene"), entry.entry_id) is None
