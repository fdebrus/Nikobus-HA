"""The blueprint, imported into a real automation and driven by a wall press."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import KEY_SWITCH_1, SetupFixture, WallSimulator, wait_for

BLUEPRINT = Path(__file__).parents[1] / "blueprints" / "automation" / "fdebrus" / "nikobus_ng_key_controls_entity.yaml"
KEY = "event.bus_push_button_4_control_buttons_n1843b4_key_1c"
TARGET = "input_boolean.other_relay"


@pytest.mark.parametrize(
    ("press", "after_first_frame"),
    [
        pytest.param("pressed", "on", id="on-press"),
        pytest.param("short_release", "off", id="on-short-release"),
    ],
)
async def test_a_key_toggles_an_entity_of_another_integration(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, tmp_path: Path, press: str, after_first_frame: str) -> None:
    folder = tmp_path / "blueprints" / "automation" / "fdebrus"
    folder.mkdir(parents=True)
    shutil.copy(BLUEPRINT, folder)
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"other_relay": {}}})
    await setup()
    assert hass.states.get(KEY).attributes["bus_address"] == KEY_SWITCH_1
    automation = {
        "use_blueprint": {
            "path": f"fdebrus/{BLUEPRINT.name}",
            "input": {"key": KEY, "press": press, "target_entities": {"entity_id": TARGET}},
        }
    }
    assert await async_setup_component(hass, "automation", {"automation": automation})
    await hass.async_block_till_done()

    await simulator.wall_press(KEY_SWITCH_1, frames=1)
    await hass.async_block_till_done()
    assert hass.states.get(TARGET).state == after_first_frame
    await wait_for(lambda: hass.states.get(TARGET).state == "on")
