"""Links said in words, and devices named as the Nikobus integration names them."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.nikobus_ng import async_remove_config_entry_device
from custom_components.nikobus_ng.const import DOMAIN
from custom_components.nikobus_ng.describe import _explained
from custom_components.nikobus_ng.llm_api import API_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from nikobus_connect.discovery import meaning
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    DIMMER,
    ENTRY_ID,
    KEY_DIMMER,
    KEY_ROLLER,
    KEY_SCENE,
    PLATE,
    ROLLER,
    SWITCH,
    SetupFixture,
)
from .test_remaining import _reloaded


async def test_a_scanned_link_is_said_in_words_with_its_values_named(hass: HomeAssistant, scanned: SetupFixture) -> None:
    """The links are the ones the scan read out of the simulated modules."""
    await scanned()
    described = await hass.services.async_call(DOMAIN, "describe", {}, blocking=True, return_response=True)
    outputs = {(o["module"], o["channel"]): o for o in described["outputs"]}
    assert outputs[(DIMMER, 1)]["controlled_by"][0]["links"] == [
        {
            "mode": "M01 (Dim on/off (2 buttons))",
            "does": "Two keys: a short press on the upper one switches the output on at its last level, "
            "on the lower one off; holding either dims up or down. Dimming on takes 4 s, dimming off 1 s.",
            "dim_on_time": "4 s",
            "dim_off_time": "1 s",
        }
    ]
    roller = next(k for k in described["keys"] if k["address"] == KEY_ROLLER)["drives"][0]
    assert (roller["module"], roller["run_time"]) == (ROLLER, "30 s")
    assert roller["does"].endswith("The motor runs for 30 s.")
    dimmer = next(k for k in described["keys"] if k["address"] == KEY_DIMMER)["drives"][0]
    assert not {"t1", "t2"} & (roller.keys() | dimmer.keys())
    scene = next(s for s in described["scenes"] if s["address"] == KEY_SCENE)
    recalled = "Light scene: a short press recalls the state stored for this output, a press of more than 3 s stores its present state."
    assert {o["does"] for o in scene["outputs"]} == {recalled}


def test_a_link_there_are_no_words_for_keeps_its_values_as_read() -> None:
    """A colour controller's link: the library describes none of its modes."""
    link = {"mode": "M19 (Start/stop scenario)", "t1": "3", "t2": None}
    assert _explained("rgb_module", link) == {"mode": "M19 (Start/stop scenario)", "t1": "3"}


@pytest.mark.parametrize(
    "name",
    [
        meaning.HOLD_TIME,
        meaning.OFF_AFTER,
        meaning.ON_AFTER,
        meaning.RUN_TIME,
        meaning.LEVEL,
        meaning.DIM_TIME,
        meaning.DIM_ON_TIME,
        meaning.DIM_OFF_TIME,
    ],
)
def test_a_model_is_told_what_each_named_value_is(name: str) -> None:
    assert f"'{name}'" in API_PROMPT


@pytest.mark.parametrize(
    ("address", "record", "plate_number", "shown"),
    [
        pytest.param(PLATE, {"name": "Boudoir", "room": "Hall", "number": 11}, None, "11: Boudoir (Hall)", id="plate"),
        pytest.param(PLATE, {"name": "Boudoir", "room": "Hall"}, 7, "7: Boudoir (Hall)", id="number-from-the-pc-link"),
        pytest.param(PLATE, {"name": "Boudoir", "room": ""}, None, "Boudoir", id="no-room-no-number"),
        pytest.param(PLATE, {"name": "", "room": "Hall", "number": 3}, None, "3: Hall", id="unnamed-plate"),
        pytest.param(SWITCH, {"name": "Board", "room": "Cellar", "number": 2}, None, "Board (Cellar)", id="module"),
        pytest.param(PLATE, {"name": "", "room": ""}, 4, None, id="nothing-to-show"),
    ],
)
async def test_a_device_is_named_as_the_nikobus_integration_names_it(
    hass: HomeAssistant, setup: SetupFixture, address: str, record: dict[str, Any], plate_number: int | None, shown: str | None
) -> None:
    inventory = (await setup()).inventory
    inventory.names["devices"][address] = record
    inventory.buttons["nikobus_button"][PLATE]["component_number"] = plate_number
    assert inventory.device_name(address) == shown


async def test_the_gateway_is_the_nikobus_bridge(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    gateway = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert gateway is not None
    assert gateway.name == "Nikobus Bridge"


async def test_a_scene_has_a_device_of_its_own_that_stays(
    hass: HomeAssistant, hass_storage: dict[str, Any], scanned: SetupFixture, entry: MockConfigEntry
) -> None:
    """Named by the project, found by the real scan; a reload runs the clean-up of lost devices."""
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["names"] = {
        "scenes": [{"name": "Dinner", "members": [[SWITCH, 5, "M14"], [SWITCH, 9, "M14"]]}]
    }
    hub = await scanned()
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, f"{KEY_SCENE}_scene"), entry.entry_id)
    assert device is not None
    assert (device.name, device.via_device_id) == ("Dinner", hub.gateway_device_id)
    await hass.config_entries.async_reload(entry.entry_id)
    await _reloaded(hass, entry, hub)
    assert devices.async_get_device_by_identifier((DOMAIN, f"{KEY_SCENE}_scene"), entry.entry_id) is not None
    # What a module holds is not forgotten by deleting its device.
    assert not await async_remove_config_entry_device(hass, entry, device)


async def test_an_output_is_described_in_the_area_of_its_device(
    hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry
) -> None:
    """The area is the module's: the entity has none of its own."""
    await setup()
    devices = dr.async_get(hass)
    module = devices.async_get_device_by_identifier((DOMAIN, SWITCH), entry.entry_id)
    assert module is not None
    devices.async_update_device(module.id, area_id=ar.async_get(hass).async_get_or_create("Cellar").id)
    described = await hass.services.async_call(DOMAIN, "describe", {}, blocking=True, return_response=True)
    assert {o["area"] for o in described["outputs"] if o["module"] == SWITCH and o["enabled"]} == {"Cellar"}
