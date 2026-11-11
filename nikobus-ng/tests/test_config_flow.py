"""The config flow against the simulator, the options flow, the project file."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.nikobus_ng.const import (
    CONF_CONNECTION,
    CONF_FEEDBACK_MODULE,
    CONF_LIGHT_OUTPUTS,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from nikobus_connect.nkb import NkbData, SceneDef
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import DIMMER, PLATE, SWITCH, SetupFixture, WallSimulator, wait_for


async def test_a_gateway_that_answers_creates_the_entry(hass: HomeAssistant, simulator: WallSimulator) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.nikobus_ng.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CONNECTION: f" {simulator.connection_string} ", CONF_FEEDBACK_MODULE: True},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_CONNECTION: simulator.connection_string, CONF_FEEDBACK_MODULE: True}
    assert result["result"].unique_id == simulator.connection_string


async def test_nothing_listening_keeps_the_form_then_recovers(hass: HomeAssistant, simulator: WallSimulator, unused_tcp_port: int) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION: f"127.0.0.1:{unused_tcp_port}", CONF_FEEDBACK_MODULE: False}
    )
    assert (result["type"], result["errors"]) == (FlowResultType.FORM, {"base": "cannot_connect"})
    with patch("custom_components.nikobus_ng.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION: simulator.connection_string, CONF_FEEDBACK_MODULE: False}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_second_entry_is_refused(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "single_instance_allowed")


async def test_reconfigure_keeps_an_unchanged_connection_without_trying_it(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    result = await entry.start_reconfigure_flow(hass)
    with patch("custom_components.nikobus_ng.config_flow._async_test") as tried:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION: entry.data[CONF_CONNECTION], CONF_FEEDBACK_MODULE: True}
        )
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure_successful")
    tried.assert_not_called()
    assert entry.data[CONF_FEEDBACK_MODULE] is True
    await hass.async_block_till_done()


async def test_reconfigure_refuses_a_connection_that_does_not_answer(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry, unused_tcp_port: int) -> None:
    await setup()
    old = entry.data[CONF_CONNECTION]
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION: f"127.0.0.1:{unused_tcp_port}", CONF_FEEDBACK_MODULE: False}
    )
    assert (result["type"], result["errors"]) == (FlowResultType.FORM, {"base": "cannot_connect"})
    assert entry.data[CONF_CONNECTION] == old


async def test_the_feedback_module_option_reloads_the_entry(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    assert hub.feedback_module is False
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_FEEDBACK_MODULE: True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    assert entry.runtime_data.feedback_module is True


@pytest.mark.parametrize("option", [pytest.param(False, id="option-unchanged"), pytest.param(True, id="option-changed-too")])
async def test_a_project_file_names_devices_and_outputs(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry, option: bool) -> None:
    hub = await setup()
    project = NkbData(
        addresses={SWITCH: ("Switch board", "Cellar"), PLATE: ("Front door", "Hall")},
        scenes=[SceneDef("Dinner", frozenset({(SWITCH, 5, "M14")}))],
        outputs={(SWITCH, 1): "Porch light"},
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch("custom_components.nikobus_ng.config_flow._read_names", return_value=project):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_FEEDBACK_MODULE: option, "project_file": "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    await hass.async_block_till_done()
    inventory = entry.runtime_data.inventory
    assert inventory.channel_name(SWITCH, 1) == "Porch light"
    assert (inventory.device_name(PLATE), inventory.device_area(PLATE)) == ("Front door", "Hall")
    scene = {"outputs": [{"module_address": SWITCH, "channel": 5, "mode": "M14 (Light scene on)"}]}
    assert inventory.scene_name(scene) == "Dinner"
    # A device already registered keeps its name; a new entity takes the output's.
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, SWITCH), entry.entry_id) is not None


async def test_a_relay_ticked_as_a_light_becomes_a_light(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    registry = er.async_get(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_FEEDBACK_MODULE: False, CONF_LIGHT_OUTPUTS: [f"{SWITCH}:2"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_2") is None
    assert registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_1") is not None
    light = registry.async_get_entity_id("light", DOMAIN, f"{SWITCH}_2")
    await wait_for(lambda: hass.states.get(light).state == "off")
    assert hass.states.get(light).attributes["supported_color_modes"] == ["onoff"]
    await hass.services.async_call("light", "turn_on", {"entity_id": light}, blocking=True)
    assert simulator.output(SWITCH, 2) == 0xFF
    assert hass.states.get(light).state == "on"


async def test_a_relay_unticked_is_a_switch_again(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(entry, options={CONF_FEEDBACK_MODULE: False, CONF_LIGHT_OUTPUTS: [f"{SWITCH}:2"]})
    hub = await setup()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("light", DOMAIN, f"{SWITCH}_2") is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_FEEDBACK_MODULE: False, CONF_LIGHT_OUTPUTS: []}
    )
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("light", DOMAIN, f"{SWITCH}_2") is None
    assert registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_2") is not None
    # A dimmer output has the same kind of id and is not a relay: it stays.
    assert registry.async_get_entity_id("light", DOMAIN, f"{DIMMER}_2") is not None


async def test_reconfigure_keeps_the_relays_that_are_lights(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(entry, options={CONF_FEEDBACK_MODULE: False, CONF_LIGHT_OUTPUTS: [f"{SWITCH}:2"]})
    await setup()
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION: entry.data[CONF_CONNECTION], CONF_FEEDBACK_MODULE: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert dict(entry.options) == {CONF_LIGHT_OUTPUTS: [f"{SWITCH}:2"]}
    await hass.async_block_till_done()


async def test_a_file_that_is_not_a_project_keeps_the_form(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch("custom_components.nikobus_ng.config_flow._read_names", side_effect=ValueError("not a zip")):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_FEEDBACK_MODULE: False, "project_file": "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"}
        )
    assert (result["type"], result["errors"]) == (FlowResultType.FORM, {"base": "invalid_project_file"})
