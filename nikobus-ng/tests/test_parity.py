"""Logical inputs, the actions, shutters set up in the options, modules that are gone."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.nikobus_ng.const import CONF_SHUTTERS, DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

from .conftest import (
    ENTRY_ID,
    PLATE,
    ROLLER,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)

INPUT = "64A061"
INPUT_ON, INPUT_OFF = "8A0C19", "CA0C19"


def _with_logical_input(hass_storage: dict[str, Any]) -> None:
    """A PC-Logic input, filed the way the library's merge files one."""
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["buttons"]["nikobus_button"][INPUT] = {
        "type": "PC-Logic Logical Input",
        "model": "05-201",
        "channels": 2,
        "description": "PC-Logic Logical Input #N64A061",
        "pc_logic_parent_address": "64A0",
        "pc_logic_parent_type": "pc_logic",
        "pc_logic_slot_index": 1,
        "operation_points": {"1A": {"bus_address": INPUT_ON}, "1B": {"bus_address": INPUT_OFF}},
    }


async def test_a_logical_input_is_a_switch_that_follows_its_two_frames(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    _with_logical_input(hass_storage)
    await setup()
    entity_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{INPUT}_input")
    assert hass.states.get(entity_id).state == "off"
    await simulator.wall_press(INPUT_ON)
    await wait_for(lambda: hass.states.get(entity_id).state == "on")
    await simulator.wall_press(INPUT_OFF)
    await wait_for(lambda: hass.states.get(entity_id).state == "off")


async def test_a_logical_input_turned_on_sends_its_closing_frame(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    _with_logical_input(hass_storage)
    await setup()
    entity_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{INPUT}_input")
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    await wait_for(lambda: INPUT_ON in simulator.host_presses)
    assert hass.states.get(entity_id).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    await wait_for(lambda: INPUT_OFF in simulator.host_presses)
    assert hass.states.get(entity_id).state == "off"


async def test_press_key_puts_any_address_on_the_bus(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    await hass.services.async_call(DOMAIN, "press_key", {"address": "3a41c2"}, blocking=True)
    await wait_for(lambda: "3A41C2" in simulator.host_presses)


async def test_read_module_reads_now(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    simulator.gateway.modules[SWITCH].outputs[2] = 0xFF
    await hass.services.async_call(DOMAIN, "read_module", {"address": SWITCH}, blocking=True)
    assert hass.states.get("switch.switch_module_4707_output_3").state == "on"


async def test_read_module_refuses_an_address_no_module_has(hass: HomeAssistant, setup: SetupFixture) -> None:
    await setup()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "read_module", {"address": "FFFF"}, blocking=True)


async def test_the_actions_say_so_when_the_entry_is_not_loaded(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "press_key", {"address": "0B7086"}, blocking=True)


async def _shutter_options(hass: HomeAssistant, entry: MockConfigEntry, settings: dict[str, Any]) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "shutter"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"output": f"{ROLLER}:2"})
    assert result["step_id"] == "shutter_settings"
    result = await hass.config_entries.options.async_configure(result["flow_id"], settings)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_a_roller_output_wired_to_a_load_is_a_switch(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("cover", DOMAIN, f"{ROLLER}_2") is not None
    await _shutter_options(hass, entry, {"use_as": "switch"})
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("cover", DOMAIN, f"{ROLLER}_2") is None
    switch = registry.async_get_entity_id("switch", DOMAIN, f"{ROLLER}_2")
    await wait_for(lambda: hass.states.get(switch).state == "off")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    # The first of the output's two relays, the one a load is wired to.
    assert simulator.output(ROLLER, 2) == 0x01
    assert hass.states.get(switch).state == "on"


async def test_the_times_of_a_shutter_set_in_the_options_are_the_ones_used(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    settings = {"use_as": "cover", "open_time": 22, "close_time": 19.5, "end_margin": 1.5}
    await _shutter_options(hass, entry, settings)
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    await hass.async_block_till_done()
    assert entry.options[CONF_SHUTTERS] == {f"{ROLLER}:2": settings}
    cover = hass.data["domain_entities"]["cover"]["cover.roller_shutter_module_9105_output_2"]
    assert (cover._travel.time_up, cover._travel.time_down, cover._end_margin) == (22, 19.5, 1.5)


@pytest.mark.usefixtures("quick_timeouts")
async def test_a_module_that_stays_silent_is_a_repair_that_removes_it(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry, hass_client: ClientSessionGenerator) -> None:
    assert await async_setup_component(hass, "repairs", {})
    hub = await setup()
    issue_id = f"module_not_answering_{ROLLER.lower()}"
    del simulator.gateway.modules[ROLLER]
    for _ in range(3):
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        await hub._async_poll()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).is_fixable

    client = await hass_client()
    response = await client.post("/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id})
    flow = await response.json()
    response = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, ROLLER), entry.entry_id) is None
    assert er.async_get(hass).async_get_entity_id("cover", DOMAIN, f"{ROLLER}_1") is None
    assert ROLLER not in hub.inventory.addresses()
    assert all(address != ROLLER for address, _ in hub.inventory.output_groups())


@pytest.mark.usefixtures("quick_timeouts")
async def test_a_module_that_answers_again_clears_its_repair(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    hub = await setup()
    module = simulator.gateway.modules.pop(ROLLER)
    for _ in range(3):
        await hub._async_poll()
    issue_id = f"module_not_answering_{ROLLER.lower()}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    simulator.gateway.modules[ROLLER] = module
    await hub._async_poll()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_deleting_a_plate_forgets_it(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator) -> None:
    assert await async_setup_component(hass, "config", {})
    hub = await setup()
    devices = dr.async_get(hass)
    plate = devices.async_get_device_by_identifier((DOMAIN, PLATE), entry.entry_id)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "config/device_registry/remove_config_entry", "config_entry_id": entry.entry_id, "device_id": plate.id}
    )
    assert (await client.receive_json())["success"]
    assert PLATE not in hub.inventory.addresses()
    assert devices.async_get_device_by_identifier((DOMAIN, PLATE), entry.entry_id) is None


async def test_the_gateway_cannot_be_deleted(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator) -> None:
    assert await async_setup_component(hass, "config", {})
    await setup()
    gateway = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "config/device_registry/remove_config_entry", "config_entry_id": entry.entry_id, "device_id": gateway.id}
    )
    assert not (await client.receive_json())["success"]


async def test_a_device_the_inventory_lost_is_removed_at_setup(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    devices = dr.async_get(hass)
    gone = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "1D0C02")}, name="Old plate")
    await setup()
    assert devices.async_get(gone.id) is None
    assert devices.async_get_device_by_identifier((DOMAIN, PLATE), entry.entry_id) is not None
