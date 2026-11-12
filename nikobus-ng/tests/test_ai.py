"""The installation as data, the tools for a language model, and the take-over from Nikobus."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from custom_components.nikobus_ng.const import CONF_LIGHT_OUTPUTS, DOMAIN
from custom_components.nikobus_ng.takeover import _carried_name
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    DIMMER,
    KEY_SCENE,
    KEY_SWITCH_1,
    PLATE,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)
from .test_remaining import _options, _reloaded

SWITCH_1 = "switch.switch_module_4707_output_1"
KEY_EVENT = "event.bus_push_button_4_control_buttons_n1843b4_key_1c"


async def _tool(hass: HomeAssistant, name: str, **args: Any) -> dict[str, Any]:
    """Call one of the tools the way Home Assistant does once it has found it.

    Home Assistant's own wrapper also writes a line in the conversation
    trace, which needs the conversation component; the tool is called
    directly here, with the arguments a model would send.
    """
    context = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, DOMAIN, context)
    tool = next(tool for tool in api.tools if tool.name == name)
    result = await tool.async_call(hass, llm.ToolInput(tool_name=name, tool_args=args), context)
    assert isinstance(result, llm.ToolResult)
    return dict(result.data)


async def test_describe_returns_the_wiring(hass: HomeAssistant, scanned: SetupFixture) -> None:
    await scanned()
    described = await hass.services.async_call(DOMAIN, "describe", {}, blocking=True, return_response=True)
    assert described["health"]["connected"] is True
    output = next(o for o in described["outputs"] if (o["module"], o["channel"]) == (SWITCH, 1))
    assert (output["entity_id"], output["module_type"], output["value"]) == (SWITCH_1, "switch_module", 0)
    assert [(k["key"], k["address"], k["links"]) for k in output["controlled_by"]] == [
        ("1C", KEY_SWITCH_1, [{"mode": "M05 (Impulse)", "does": "Each press switches the output over, on or off."}])
    ]
    key = next(k for k in described["keys"] if k["address"] == KEY_SWITCH_1)
    assert (key["plate_address"], key["entity_id"]) == (PLATE, KEY_EVENT)
    assert [(d["entity_id"], d["mode"]) for d in key["drives"]] == [(SWITCH_1, "M05 (Impulse)")]
    scene = next(s for s in described["scenes"] if s["address"] == KEY_SCENE)
    assert sorted(o["channel"] for o in scene["outputs"]) == [5, 9]


async def test_the_tools_are_offered_as_an_llm_api_while_the_entry_is_loaded(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    api = next(api for api in llm.async_get_apis(hass) if api.id == DOMAIN)
    context = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    instance = await api.async_get_api_instance(context)
    assert [tool.name for tool in instance.tools] == [
        "nikobus_overview",
        "nikobus_output",
        "nikobus_key",
        "nikobus_scenes",
        "nikobus_health",
        "nikobus_press_key",
    ]
    # Only the press writes, and Home Assistant is told which tool each is.
    assert [tool.name for tool in instance.tools if not tool.annotations.read_only] == ["nikobus_press_key"]
    assert all(tool.integration == DOMAIN for tool in instance.tools)
    await hass.config_entries.async_unload(entry.entry_id)
    assert all(api.id != DOMAIN for api in llm.async_get_apis(hass))


async def test_a_model_can_ask_what_drives_an_output_and_what_a_key_does(hass: HomeAssistant, scanned: SetupFixture) -> None:
    await scanned()
    overview = await _tool(hass, "nikobus_overview")
    assert {"entity_id": SWITCH_1, "keys": 1}.items() <= next(o for o in overview["outputs"] if o["entity_id"] == SWITCH_1).items()
    output = await _tool(hass, "nikobus_output", entity_id=SWITCH_1)
    assert output["controlled_by"][0]["address"] == KEY_SWITCH_1
    for reference in (KEY_EVENT, KEY_SWITCH_1.lower()):
        key = await _tool(hass, "nikobus_key", key=reference)
        assert [d["entity_id"] for d in key["drives"]] == [SWITCH_1]
    assert (await _tool(hass, "nikobus_health"))["connected"] is True


async def test_a_model_is_told_when_it_asks_about_something_else(hass: HomeAssistant, setup: SetupFixture) -> None:
    await setup()
    with pytest.raises(HomeAssistantError):
        await _tool(hass, "nikobus_output", entity_id="light.of_another_integration")
    with pytest.raises(HomeAssistantError):
        await _tool(hass, "nikobus_key", key="FFFFFF")


async def test_the_press_tool_puts_the_key_on_the_bus(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    assert await _tool(hass, "nikobus_press_key", address=KEY_SWITCH_1.lower()) == {"pressed": KEY_SWITCH_1}
    await wait_for(lambda: simulator.host_presses == [KEY_SWITCH_1])


def _nikobus_entities(hass: HomeAssistant) -> MockConfigEntry:
    """What a disabled Nikobus entry leaves in the registry, under the unique ids it gives:
    a relay shown as a light, a dimmer, a key's sensor and press button, a sensor of its bridge."""
    old = MockConfigEntry(domain="nikobus", title="Nikobus")
    old.add_to_hass(hass)
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    light = registry.async_get_or_create(
        "light", "nikobus", f"nikobus_light_relay_switch_{SWITCH}_2", config_entry=old,
        suggested_object_id="kitchen_ceiling", original_name="Kitchen ceiling",
    )
    registry.async_update_entity(light.entity_id, area_id=area.id, aliases={"Big light"}, labels={"nikobus_output"}, icon="mdi:ceiling-light")
    registry.async_update_entity_options(light.entity_id, "conversation", {"should_expose": True})
    registry.async_get_or_create(
        "light", "nikobus", f"nikobus_light_dimmer_light_{DIMMER}_1", config_entry=old,
        suggested_object_id="living_spots", original_name="Living spots",
    )
    key = registry.async_get_or_create(
        "binary_sensor", "nikobus", f"nikobus_button_{KEY_SWITCH_1}", config_entry=old,
        suggested_object_id="bt_hall_ceiling_up", original_name="Hall Key 1C",
    )
    registry.async_update_entity(key.entity_id, area_id=area.id, labels={"nikobus_button"})
    registry.async_get_or_create(
        "button", "nikobus", f"nikobus_push_button_{KEY_SWITCH_1}", config_entry=old,
        suggested_object_id="bt_hall_ceiling_up",
    )
    registry.async_get_or_create(
        "sensor", "nikobus", "nikobus_pc_link_clock", config_entry=old, suggested_object_id="bridge_clock"
    )
    # An output no key drives, used from Home Assistant only. Its entry is disabled, and so is it.
    registry.async_get_or_create(
        "light", "nikobus", f"nikobus_light_relay_switch_{SWITCH}_3", config_entry=old,
        suggested_object_id="attic_fan", original_name="Attic fan",
        disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY,
    )
    return old


async def test_a_take_over_moves_ids_names_areas_and_exposure_and_can_be_undone(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    old = _nikobus_entities(hass)
    hub = await setup()
    registry = er.async_get(hass)
    before = registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_2")
    assert before == "switch.switch_module_4707_output_2"

    result = await _options(hass, entry, {"next_step_id": "take_over"})
    # As an output no key drives is created on a new installation: disabled.
    registry.async_update_entity("switch.switch_module_4707_output_3", disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    assert result["description_placeholders"] == {"entities": "5", "left": "1", "lights": "2"}
    # Nothing has moved yet: the form only says what would.
    assert registry.async_get("light.kitchen_ceiling").platform == "nikobus"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    taken = await _reloaded(hass, entry, hub)

    # The relay is a light here too, and carries the id, the name and what went with it.
    assert entry.options[CONF_LIGHT_OUTPUTS] == [f"{SWITCH}:2", f"{SWITCH}:3"]
    # Disabled there only because its entry is: it was in use, so it is enabled here.
    assert registry.async_get("light.attic_fan").disabled_by is None
    assert registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_2") is None
    mine = registry.async_get("light.kitchen_ceiling")
    assert (mine.platform, mine.unique_id) == (DOMAIN, f"{SWITCH}_2")
    assert (mine.aliases, mine.labels, mine.icon) == ({"Big light"}, {"nikobus_output"}, "mdi:ceiling-light")
    assert mine.options["conversation"] == {"should_expose": True}
    state = hass.states.get("light.kitchen_ceiling")
    assert (state.name, state.state) == ("Kitchen ceiling", "off")
    assert ar.async_get(hass).async_get_area(mine.area_id).name == "Kitchen"
    assert hass.states.get("light.living_spots").name == "Living spots"
    # The Nikobus entities are parked, not deleted.
    assert registry.async_get("light.kitchen_ceiling_nikobus").platform == "nikobus"
    # A key is an event entity here: it takes the sensor's id under its own domain, and the sensor keeps its id.
    event = registry.async_get("event.bt_hall_ceiling_up")
    assert (event.platform, event.unique_id, event.labels) == (DOMAIN, f"{KEY_SWITCH_1}_event", {"nikobus_button"})
    assert hass.states.get("event.bt_hall_ceiling_up").name == "Hall Key 1C"
    assert ar.async_get(hass).async_get_area(event.area_id).name == "Kitchen"
    assert registry.async_get("binary_sensor.bt_hall_ceiling_up").platform == "nikobus"
    # Its press button changes hands like an output, and is enabled here as it was there.
    press = registry.async_get("button.bt_hall_ceiling_up")
    assert (press.platform, press.unique_id, press.disabled_by) == (DOMAIN, f"{KEY_SWITCH_1}_press", None)
    assert registry.async_get("button.bt_hall_ceiling_up_nikobus").platform == "nikobus"
    # And it is the real output behind the taken-over id.
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.kitchen_ceiling"}, blocking=True)
    assert simulator.output(SWITCH, 2) == 0xFF

    # Nothing is left to take, so it is not offered; an entity that turns up later is, alone.
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert "take_over" not in menu["menu_options"]
    hass.config_entries.options.async_abort(menu["flow_id"])
    registry.async_get_or_create(
        "light", "nikobus", f"nikobus_light_relay_switch_{SWITCH}_4", config_entry=old, suggested_object_id="porch"
    )
    again = await _options(hass, entry, {"next_step_id": "take_over"})
    assert again["description_placeholders"]["entities"] == "1"
    hass.config_entries.options.async_abort(again["flow_id"])
    registry.async_remove("light.porch")

    result = await _options(hass, taken.entry, {"next_step_id": "give_back"}, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    given = await _reloaded(hass, entry, taken)
    assert registry.async_get("light.kitchen_ceiling").platform == "nikobus"
    assert registry.async_get("light.kitchen_ceiling_nikobus") is None
    assert registry.async_get("button.bt_hall_ceiling_up").platform == "nikobus"
    assert registry.async_get_entity_id("event", DOMAIN, f"{KEY_SWITCH_1}_event") == KEY_EVENT
    assert registry.async_get_entity_id("light", DOMAIN, f"{DIMMER}_1") == "light.dim_controller_module_0e6c_output_1"
    assert given.inventory.takeover == {"pending": [], "done": []}


async def test_a_take_over_is_refused_while_nikobus_runs(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    old = _nikobus_entities(hass)
    await setup()
    old.mock_state(hass, ConfigEntryState.LOADED)
    result = await _options(hass, entry, {"next_step_id": "take_over"})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "nikobus_running")


async def test_without_nikobus_entities_there_is_nothing_to_take_over(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "take_over" not in result["menu_options"]
    assert "give_back" not in result["menu_options"]


@pytest.mark.parametrize(
    ("name", "original", "mine", "carried"),
    [
        pytest.param("Wall lights", "Salon Appliques", "Output 1", "Wall lights", id="a-name-a-person-gave"),
        pytest.param(None, "Bedroom shutter", "Output 3", "Bedroom shutter", id="the-name-nikobus-gave"),
        pytest.param("Wall lights", None, "Wall lights", None, id="already-this-entitys-name"),
        pytest.param("", None, None, None, id="named-by-its-device"),
    ],
)
def test_from_2026_11_only_the_entitys_own_part_of_the_name_is_carried(
    name: str | None, original: str | None, mine: str | None, carried: str | None
) -> None:
    """Home Assistant 2026.11 puts the device's name in front of a name set in the registry.

    The tests run on 2026.10, whose registry entry has no ``compat_name``;
    the 2026.11 entry is stood in for by the three fields read here. Not
    modelled: the registry itself, and how 2026.11 then shows the name.
    """
    old = SimpleNamespace(name=name, original_name=original, compat_name=None)
    assert _carried_name(old, SimpleNamespace(original_name=mine), "Module Wall lights") == carried


def test_up_to_2026_10_the_whole_shown_name_is_carried() -> None:
    old = SimpleNamespace(name=None, original_name="Bedroom shutter")
    assert _carried_name(old, SimpleNamespace(original_name="Output 3"), "Module Bedroom shutter") == "Module Bedroom shutter"


async def test_an_entity_an_earlier_take_over_left_disabled_is_enabled(
    hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry
) -> None:
    """Up to 0.8.0 an entity whose Nikobus entry was disabled counted as unused."""
    old = MockConfigEntry(domain="nikobus", title="Nikobus")
    old.add_to_hass(hass)
    registry = er.async_get(hass)
    parked = registry.async_get_or_create(
        "light", "nikobus", f"nikobus_light_relay_switch_{SWITCH}_3", config_entry=old,
        suggested_object_id="attic_fan_nikobus", disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY,
    )
    hub = await setup()
    mine = registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_3")
    # As an output no key drives is created on a new installation: disabled.
    registry.async_update_entity(mine, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    hub.inventory.takeover["done"] = [
        {"entity_id": "light.attic_fan", "domain": "switch", "unique_id": f"{SWITCH}_3", "name": "", "previous_entity_id": mine, "parked": parked.entity_id}
    ]
    await hub.inventory.async_save()
    await hass.config_entries.async_reload(entry.entry_id)
    await _reloaded(hass, entry, hub)
    assert registry.async_get(mine).disabled_by is None
