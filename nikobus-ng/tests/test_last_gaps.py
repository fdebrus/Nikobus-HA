"""Module types set by hand, an old PC-Link, press repeats, calendar channels, files, scenes at the wall."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.nikobus_ng.const import (
    CONF_LED_KEYS,
    CONF_LIGHT_OUTPUTS,
    CONF_MODULE_TYPES,
    CONF_OLD_PC_LINK,
    CONF_PRESS_REPEAT,
    CONF_SHUTTERS,
    DOMAIN,
)
from custom_components.nikobus_ng.manual import inventory_from_files
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from nikobus_connect import NikobusAPI
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import (
    ENTRY_ID,
    KEY_SCENE,
    KEY_SWITCH_1,
    PLATE,
    ROLLER,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)
from .test_remaining import _options, _reloaded, _rescanned

MODULES = {
    "switch_module": [
        {
            "address": SWITCH,
            "description": "Board",
            "model": "05-000-02",
            "channels": [
                {"description": "Kitchen ceiling", "entity_type": "light"},
                {"description": "Garden", "led_on": KEY_SWITCH_1.lower(), "led_off": KEY_SWITCH_1},
                *({"description": f"Output {n}"} for n in range(3, 13)),
            ],
        }
    ],
    "roller_module": [
        {
            "address": ROLLER,
            "channels": [
                {"description": "Shutter", "operation_time_up": "30.5", "operation_time_down": "32", "end_stop_margin": "8"},
                {"description": "Pump", "entity_type": "switch"},
                *({"description": "not_in_use"} for _ in range(4)),
            ],
        }
    ],
}
BUTTONS = {
    "nikobus_button": [
        {"address": KEY_SWITCH_1, "description": "Hall right top", "linked_button": [{"address": PLATE, "key": "1C", "channels": 4, "type": "Push button", "model": "05-064"}]},
        {"address": "2D1C80", "description": "Garage contact"},
    ]
}


def _stored(hass_storage: dict[str, Any]) -> dict[str, Any]:
    return hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]


async def test_a_module_given_another_type_changes_entities_and_is_read_again(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("cover", DOMAIN, f"{ROLLER}_1") is not None
    result = await _options(hass, entry, {"next_step_id": "module_type"}, {"module": ROLLER, "module_type": "switch_module"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    new_hub = await _rescanned(hass, entry, hub)
    inventory = new_hub.inventory
    # A switch module has twelve outputs or four, not six: the usual twelve.
    assert (inventory.module_type(ROLLER), inventory.channel_count(ROLLER)) == ("switch_module", 12)
    assert inventory.inventoried_type(ROLLER) == "roller_module"
    assert (inventory.links_read_as, inventory.types_to_read_again()) == ({ROLLER: "switch_module"}, [])
    assert registry.async_get_entity_id("cover", DOMAIN, f"{ROLLER}_1") is None
    assert registry.async_get_entity_id("switch", DOMAIN, f"{ROLLER}_12") is not None

    result = await _options(hass, entry, {"next_step_id": "module_type"}, {"module": ROLLER, "module_type": "as_inventoried"})
    restored = await _rescanned(hass, entry, new_hub)
    assert entry.options[CONF_MODULE_TYPES] == {}
    assert (restored.inventory.module_type(ROLLER), restored.inventory.links_read_as) == ("roller_module", {})
    assert registry.async_get_entity_id("cover", DOMAIN, f"{ROLLER}_1") is not None


async def test_a_module_that_answers_as_another_type_can_be_given_it_from_the_repair(hass: HomeAssistant, hass_storage: dict[str, Any], setup: SetupFixture, entry: MockConfigEntry, hass_client: ClientSessionGenerator) -> None:
    assert await async_setup_component(hass, "repairs", {})
    # The inventory has the roller module down as a dimmer; the module itself answers as a roller.
    module = _stored(hass_storage)["modules"]["nikobus_module"][ROLLER]
    module.update(module_type="dimmer_module", channels=[{} for _ in range(12)])
    hub = await setup()
    assert hub.inventory.module_type(ROLLER) == "dimmer_module"
    with patch.object(NikobusAPI, "get_module_crc", AsyncMock(return_value=0x1234)):
        report = await hass.services.async_call(
            DOMAIN, "verify_programming", {"modules": [ROLLER]}, blocking=True, return_response=True
        )
    assert (report["modules"][ROLLER]["family"], report["modules"][ROLLER]["family_ok"]) == ("roller_module", False)
    issue_id = f"module_type_mismatch_{ROLLER.lower()}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).is_fixable

    client = await hass_client()
    response = await client.post("/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id})
    flow = await response.json()
    response = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await response.json())["type"] == "create_entry"
    fixed = await _rescanned(hass, entry, hub)
    assert entry.options[CONF_MODULE_TYPES] == {ROLLER: "roller_module"}
    assert (fixed.inventory.module_type(ROLLER), fixed.inventory.channel_count(ROLLER)) == ("roller_module", 6)
    assert er.async_get(hass).async_get_entity_id("cover", DOMAIN, f"{ROLLER}_1") is not None


async def test_an_old_pc_link_is_written_without_a_read_and_keeps_what_was_written(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    result = await _options(
        hass, entry, {"next_step_id": "gateway"}, {"feedback_module": False, CONF_OLD_PC_LINK: True, CONF_PRESS_REPEAT: 5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.state is ConfigEntryState.LOADED)
    old = entry.runtime_data
    assert (old.command.read_before_write_s, old.api.press_repeat) == (None, 5)
    switch = "switch.switch_module_4707_output_3"
    # Nothing is read: an output is unknown until Home Assistant has written it.
    assert hass.states.get(switch).state == "unknown"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert simulator.output(SWITCH, 3) == 0xFF
    assert hass.states.get(switch).state == "on"

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # What was written is taken back, so the next command does not switch it off.
    assert hass.states.get(switch).state == "on"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.switch_module_4707_output_4"}, blocking=True)
    assert (simulator.output(SWITCH, 3), simulator.output(SWITCH, 4)) == (0xFF, 0xFF)


async def test_a_press_repeats_its_telegram_as_often_as_the_options_say(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(entry, options={CONF_PRESS_REPEAT: 5})
    hub = await setup()
    assert hub.api.press_command("0B7086").count("#N0B7086") == 5
    frames: list[str] = []
    handle = simulator.gateway.handle

    def _counting(line: str) -> list[str]:
        frames.extend(part for part in line.split("\\r") if part.startswith("#N"))
        return handle(line)

    simulator.gateway.handle = _counting  # type: ignore[method-assign]
    await hass.services.async_call(DOMAIN, "press_key", {"address": "0B7086"}, blocking=True)
    await wait_for(lambda: len(frames) == 5)


async def test_a_calendar_channel_is_named_among_what_drives_an_output(hass: HomeAssistant, hass_storage: dict[str, Any], setup: SetupFixture) -> None:
    _stored(hass_storage)["buttons"]["nikobus_button"]["C0FFEE"] = {
        "type": "PC-Link calendar channel",
        "calendar_channel": "CH001A",
        "channels": 1,
        "operation_points": {
            "1A": {"bus_address": "8A0C19", "linked_modules": [{"module_address": SWITCH, "outputs": [{"channel": 3, "mode": "M01 (On / off)"}]}]}
        },
    }
    await setup()
    driven = hass.states.get("switch.switch_module_4707_output_3").attributes["controlled_by"]
    assert driven == ["PC-Link calendar channel CH001A"]
    # What the PC-Link sends for a channel is not known: it is no key to press.
    assert er.async_get(hass).async_get_entity_id("event", DOMAIN, "8A0C19_event") is None


async def test_a_scene_activated_at_the_wall_records_it(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    _stored(hass_storage)["scenes"] = {
        KEY_SCENE: {
            "bus_address": KEY_SCENE,
            "pattern": "switch_pair",
            "triggered_by": [KEY_SCENE, KEY_SWITCH_1],
            "outputs": [{"module_address": SWITCH, "channel": 5, "mode": "M14 (Light scene on)"}],
        }
    }
    await setup()
    scene = er.async_get(hass).async_get_entity_id("scene", DOMAIN, f"{KEY_SCENE}_scene")
    assert hass.states.get(scene).state == "unknown"
    # One central function can be on several keys: any of them activates it.
    await simulator.wall_press(KEY_SWITCH_1)
    await wait_for(lambda: hass.states.get(scene).state != "unknown")


def test_the_files_of_the_nikobus_integration_become_an_inventory_and_options() -> None:
    config, names, options = inventory_from_files(MODULES, BUTTONS)
    assert [m["address"] for m in config.module_config["switch_module"]] == [SWITCH]
    assert names["channels"] == {f"{SWITCH}:1": "Kitchen ceiling", f"{SWITCH}:2": "Garden", f"{ROLLER}:1": "Shutter", f"{ROLLER}:2": "Pump"}
    assert names["devices"] == {SWITCH: {"name": "Board", "room": ""}}
    assert options[CONF_LIGHT_OUTPUTS] == [f"{SWITCH}:1"]
    assert options[CONF_LED_KEYS] == {f"{SWITCH}:2": {"on_key": KEY_SWITCH_1, "off_key": KEY_SWITCH_1}}
    assert options[CONF_SHUTTERS] == {
        f"{ROLLER}:1": {"open_time": 30.5, "close_time": 32.0, "end_margin": 8.0},
        f"{ROLLER}:2": {"use_as": "switch"},
    }


@pytest.mark.parametrize(
    ("modules", "buttons"),
    [
        pytest.param([], BUTTONS, id="modules-not-an-object"),
        pytest.param({"switch_module": []}, BUTTONS, id="no-module"),
        pytest.param({"switch_module": [{"address": SWITCH, "channels": [{}] * 5}]}, BUTTONS, id="wrong-output-count"),
        pytest.param(MODULES, {"nikobus_button": [{"description": "no address"}]}, id="key-without-address"),
    ],
)
def test_files_that_are_not_an_inventory_are_refused(modules: Any, buttons: Any) -> None:
    with pytest.raises((ValueError, KeyError, TypeError)):
        inventory_from_files(modules, buttons)


async def test_two_uploaded_files_stand_in_for_the_inventory(hass: HomeAssistant, hass_storage: dict[str, Any], setup: SetupFixture, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No PC-Link and no project file: the files name the modules and keys, the links come from the modules."""
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_CLOSE_TIMEOUT", 0.2)
    _stored(hass_storage).update(modules={"nikobus_module": {}}, buttons={"nikobus_button": {}})
    hub = await setup()
    await hub.discovery.async_wait()
    result = await _options(hass, entry, {"next_step_id": "files"})
    with patch("custom_components.nikobus_ng.config_flow._read_files", return_value=json.loads(json.dumps([MODULES, BUTTONS]))):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"module_file": "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0", "button_file": "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    new_hub = await _rescanned(hass, entry, hub)
    inventory = new_hub.inventory
    assert inventory.from_project
    assert inventory.impacted_groups(KEY_SWITCH_1) == [(SWITCH, 1)]
    registry = er.async_get(hass)
    light = registry.async_get_entity_id("light", DOMAIN, f"{SWITCH}_1")
    assert hass.states.get(light).name == "Board Kitchen ceiling"
    assert registry.async_get_entity_id("switch", DOMAIN, f"{ROLLER}_2") is not None
    assert registry.async_get_entity_id("event", DOMAIN, "2D1C80_event") is not None
    assert await _reloaded(hass, entry, None) is new_hub
