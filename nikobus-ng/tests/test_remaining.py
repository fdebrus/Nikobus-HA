"""Fans, LED keys, shutter groups, audio zones, colour controllers, a project file as inventory."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from custom_components.nikobus_ng.const import (
    CONF_FAN_OUTPUTS,
    CONF_LED_KEYS,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from nikobus_connect.nkb import NkbConfig, NkbData
from nikobus_simulator.gateway import PRESS_DEBOUNCE
from nikobus_simulator.module import SimulatedModule
from nikobus_simulator.topology import ModuleSpec
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    AUDIO,
    DIMMER,
    ENTRY_ID,
    KEY_SWITCH_1,
    PLATE,
    ROLLER,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)

RGB = "801D"
RGB_ON, RGB_OFF, RGB_TOGGLE = "2A4812", "6A4812", "AA4812"
GROUP = "38800A"


def _stored(hass_storage: dict[str, Any]) -> dict[str, Any]:
    return hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]


async def _options(hass: HomeAssistant, entry: MockConfigEntry, *steps: dict[str, Any]) -> dict[str, Any]:
    """Open the options and answer the menu and the forms in turn."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for answer in steps:
        result = await hass.config_entries.options.async_configure(result["flow_id"], answer)
    return result


async def _reloaded(hass: HomeAssistant, entry: MockConfigEntry, hub: Any) -> Any:
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    new_hub = entry.runtime_data
    await wait_for(lambda: all(new_hub.value(a, 1 if g == 1 else 7) is not None for a, g in new_hub.inventory.output_groups()))
    await hass.async_block_till_done()
    return new_hub


async def _rescanned(hass: HomeAssistant, entry: MockConfigEntry, hub: Any) -> Any:
    """The hub once the entry reloaded, read links by itself, and reloaded again."""
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.state is ConfigEntryState.LOADED)
    scanning = entry.runtime_data
    await wait_for(lambda: scanning.discovery.running or entry.runtime_data is not scanning)
    await scanning.discovery.async_wait()
    return await _reloaded(hass, entry, scanning)


async def test_a_dimmer_output_ticked_as_a_fan_gets_a_speed(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    result = await _options(hass, entry, {"next_step_id": "outputs"}, {CONF_FAN_OUTPUTS: [f"{DIMMER}:2"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _reloaded(hass, entry, hub)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("light", DOMAIN, f"{DIMMER}_2") is None
    fan = registry.async_get_entity_id("fan", DOMAIN, f"{DIMMER}_2")
    assert hass.states.get(fan).state == "off"
    await hass.services.async_call("fan", "set_percentage", {"entity_id": fan, "percentage": 50}, blocking=True)
    assert simulator.output(DIMMER, 2) == 128
    assert (hass.states.get(fan).state, hass.states.get(fan).attributes["percentage"]) == ("on", 50)
    await hass.services.async_call("fan", "turn_off", {"entity_id": fan}, blocking=True)
    assert simulator.output(DIMMER, 2) == 0


async def test_an_output_with_an_led_key_is_switched_by_that_key(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nikobus_connect.api.FEEDBACK_KEY_SETTLE_S", 0.05)
    hub = await setup()
    result = await _options(
        hass, entry, {"next_step_id": "led_key"}, {"output": f"{SWITCH}:1"}, {"on_key": KEY_SWITCH_1.lower(), "off_key": KEY_SWITCH_1}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LED_KEYS] == {f"{SWITCH}:1": {"on_key": KEY_SWITCH_1, "off_key": KEY_SWITCH_1}}
    await _reloaded(hass, entry, hub)
    switch = "switch.switch_module_4707_output_1"

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert simulator.host_presses == [KEY_SWITCH_1]
    assert simulator.output(SWITCH, 1) == 0xFF
    assert hass.states.get(switch).state == "on"
    # Already on: the key is left alone, or its LED would be inverted.
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert simulator.host_presses == [KEY_SWITCH_1]
    # An output without a key is still written directly.
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.switch_module_4707_output_2"}, blocking=True)
    assert simulator.output(SWITCH, 2) == 0xFF
    assert simulator.host_presses == [KEY_SWITCH_1]

    # A module takes one key's frames within 0.3 s for one press.
    await asyncio.sleep(PRESS_DEBOUNCE + 0.05)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert simulator.host_presses == [KEY_SWITCH_1, KEY_SWITCH_1]
    assert simulator.output(SWITCH, 1) == 0x00
    assert hass.states.get(switch).state == "off"


async def test_an_led_key_must_be_an_address(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    result = await _options(hass, entry, {"next_step_id": "led_key"}, {"output": f"{SWITCH}:1"}, {"on_key": "the hall key"})
    assert (result["type"], result["errors"]) == (FlowResultType.FORM, {"base": "invalid_key_address"})


async def test_a_function_that_only_moves_shutters_is_a_group_cover(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    _stored(hass_storage)["scenes"] = {
        GROUP: {
            "bus_address": GROUP,
            "pattern": "roller_pair",
            "outputs": [
                {"module_address": ROLLER, "channel": channel, "mode": "M01 (Open - stop - close)", "t1": "30s"}
                for channel in (1, 2)
            ],
        }
    }
    await setup()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("scene", DOMAIN, f"{GROUP}_scene") is None
    group = registry.async_get_entity_id("cover", DOMAIN, f"{GROUP}_group")
    members = ["cover.roller_shutter_module_9105_output_1", "cover.roller_shutter_module_9105_output_2"]
    assert hass.states.get(group).attributes["entity_id"] == members
    assert hass.states.get(group).state == "open"

    await hass.services.async_call("cover", "close_cover", {"entity_id": group}, blocking=True)
    assert (simulator.output(ROLLER, 1), simulator.output(ROLLER, 2)) == (0x02, 0x02)
    await hass.async_block_till_done()
    assert hass.states.get(group).state == "closing"
    await hass.services.async_call("cover", "stop_cover", {"entity_id": group}, blocking=True)
    assert (simulator.output(ROLLER, 1), simulator.output(ROLLER, 2)) == (0x00, 0x00)


async def test_the_zone_an_audio_module_holds_is_a_media_player(hass: HomeAssistant, simulator: WallSimulator, scanned: SetupFixture) -> None:
    """The triggers come out of the module through the real link scan."""
    hub = await scanned()
    zones = hub.inventory.audio_zones()
    assert list(zones) == [(AUDIO, 1)]
    frames = zones[(AUDIO, 1)]
    player = er.async_get(hass).async_get_entity_id("media_player", DOMAIN, f"{AUDIO}_zone_1")
    assert hass.states.get(player).attributes["source_list"] == [f"Source {n}" for n in range(1, 5)]

    await hass.services.async_call("media_player", "turn_on", {"entity_id": player}, blocking=True)
    await wait_for(lambda: frames["M16 (On)"] in simulator.host_presses)
    assert hass.states.get(player).state == "on"
    await hass.services.async_call("media_player", "select_source", {"entity_id": player, "source": "Source 2"}, blocking=True)
    await wait_for(lambda: frames["M04 (Source 2)"] in simulator.host_presses)
    assert hass.states.get(player).attributes["source"] == "Source 2"
    # A wall key sending the zone's off frame is followed too.
    await simulator.wall_press(frames["M17 (Off)"])
    await wait_for(lambda: hass.states.get(player).state == "off")


def _with_rgb(hass_storage: dict[str, Any], simulator: WallSimulator, links: list[dict[str, str]]) -> SimulatedModule:
    """A colour controller in the inventory, and something that answers its state query.

    The simulator has no colour controller. A six-output module stands
    in for it: it answers the state query, which is all a real one
    answers; that its keys change nothing here is set by the test.
    """
    _stored(hass_storage)["modules"]["nikobus_module"][RGB] = {
        "module_type": "rgb_module",
        "description": "RGB controller",
        "model": "340-00112",
        "rgb_links": links,
    }
    module = SimulatedModule(ModuleSpec(address=RGB, type="switch_module", channels=6, links=[]))
    simulator.gateway.modules[RGB] = module
    return module


async def test_a_colour_controller_is_read_and_switched_by_its_keys(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    links = [{"bus_address": RGB_ON, "role": "on"}, {"bus_address": RGB_OFF, "role": "off"}]
    module = _with_rgb(hass_storage, simulator, links)
    # On, and of the three colour outputs only the first lit.
    module.outputs[:4] = bytes([0xFF, 0xFF, 0x00, 0x00])
    await setup()
    light = er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{RGB}_rgb")
    state = hass.states.get(light)
    assert (state.state, state.attributes["colour"], state.attributes["off_key"]) == ("on", "red", RGB_OFF)
    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    await wait_for(lambda: simulator.host_presses == [RGB_OFF])


async def test_a_toggle_key_is_not_pressed_when_the_controller_is_already_there(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    _with_rgb(hass_storage, simulator, [{"bus_address": RGB_TOGGLE, "role": "toggle"}])
    await setup()
    light = er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{RGB}_rgb")
    assert hass.states.get(light).state == "off"
    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    assert simulator.host_presses == []
    await hass.services.async_call("light", "turn_on", {"entity_id": light}, blocking=True)
    await wait_for(lambda: simulator.host_presses == [RGB_TOGGLE])


async def test_a_colour_controller_without_a_known_key_says_so(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    _with_rgb(hass_storage, simulator, [])
    await setup()
    light = er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{RGB}_rgb")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("light", "turn_on", {"entity_id": light}, blocking=True)


async def test_a_project_file_stands_in_for_the_inventory_of_a_pc_link(hass: HomeAssistant, hass_storage: dict[str, Any], setup: SetupFixture, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch) -> None:
    """No PC-Link answers the simulator's inventory: the file names the modules and plates.

    Which key drives which output is then read from the module by the real link scan.
    """
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_CLOSE_TIMEOUT", 0.2)
    _stored(hass_storage).update(modules={"nikobus_module": {}}, buttons={"nikobus_button": {}})
    hub = await setup()
    await hub.discovery.async_wait()
    assert hub.discovery.status.error == "no_pc_link"

    project = NkbConfig(
        module_config={
            "switch_module": [
                {"address": SWITCH, "description": "Board", "model": "05-000-02", "channels": [{"description": f"Output {n}"} for n in range(1, 13)]}
            ]
        },
        button_config={
            "nikobus_button": [
                {"address": KEY_SWITCH_1, "description": "Hall (1C)", "linked_button": [{"address": PLATE, "key": "1C", "channels": 4, "type": "Push button", "model": "05-064"}]}
            ]
        },
    )
    names = NkbData(addresses={}, scenes=[], outputs={})
    result = await _options(hass, entry, {"next_step_id": "names"})
    assert "use_as_inventory" in {str(key) for key in result["data_schema"].schema}
    with patch("custom_components.nikobus_ng.config_flow._read_names", return_value=(names, project)):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"project_file": "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0", "rooms_as_areas": False, "use_as_inventory": True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    new_hub = await _rescanned(hass, entry, hub)
    inventory = new_hub.inventory
    assert inventory.from_project
    assert [module.address for module in inventory.output_modules()] == [SWITCH]
    assert inventory.impacted_groups(KEY_SWITCH_1) == [(SWITCH, 1)]
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_1") is not None
    assert registry.async_get_entity_id("event", DOMAIN, f"{KEY_SWITCH_1}_event") is not None
