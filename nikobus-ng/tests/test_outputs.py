"""Commands reach the module, and the entity shows what the module holds."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import DIMMER, SWITCH, SetupFixture, WallSimulator, wait_for

SWITCH_1 = "switch.switch_module_4707_output_1"
SWITCH_9 = "switch.switch_module_4707_output_9"
LIGHT_1 = "light.dim_controller_module_0e6c_output_1"


async def test_a_switch_is_written_and_acknowledged(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH_1}, blocking=True)
    # The call returns once the module has acknowledged the frame.
    assert simulator.output(SWITCH, 1) == 0xFF
    assert hass.states.get(SWITCH_1).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH_1}, blocking=True)
    assert simulator.output(SWITCH, 1) == 0x00
    assert hass.states.get(SWITCH_1).state == "off"


async def test_the_second_group_is_written_to_the_second_group(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH_9}, blocking=True)
    assert simulator.output(SWITCH, 9) == 0xFF
    assert [simulator.output(SWITCH, channel) for channel in range(1, 7)] == [0] * 6


async def test_a_write_keeps_what_the_wall_changed_meanwhile(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    """A frame carries six outputs: the group is read before it is written."""
    hub = await setup()
    simulator.gateway.modules[SWITCH].outputs[1] = 0xFF  # channel 2, behind Home Assistant's back
    hub.command.invalidate_group_state(SWITCH, 1)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH_1}, blocking=True)
    assert (simulator.output(SWITCH, 1), simulator.output(SWITCH, 2)) == (0xFF, 0xFF)
    assert hass.states.get("switch.switch_module_4707_output_2").state == "on"


async def test_a_dimmer_takes_a_level(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT_1, "brightness": 120}, blocking=True)
    assert simulator.output(DIMMER, 1) == 120
    state = hass.states.get(LIGHT_1)
    assert (state.state, state.attributes["brightness"]) == ("on", 120)
    await hass.services.async_call("light", "turn_off", {"entity_id": LIGHT_1}, blocking=True)
    assert simulator.output(DIMMER, 1) == 0


async def test_a_command_while_disconnected_is_refused(hass: HomeAssistant, setup: SetupFixture) -> None:
    hub = await setup()
    hub.connected = False
    with pytest.raises(HomeAssistantError):
        await hub.async_set_output(SWITCH, 1, 0xFF)
    with pytest.raises(HomeAssistantError):
        await hub.async_press("0B7086")


async def test_entities_go_unavailable_and_come_back_with_the_connection(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.nikobus_ng.hub.RECONNECT_DELAY_INITIAL", 0.05)
    hub = await setup()
    for writer in list(simulator._clients):
        writer.close()  # the line drops
    await wait_for(lambda: hass.states.get(SWITCH_1).state == "unavailable")
    assert hass.states.get("binary_sensor.nikobus_connection").state == "off"
    await wait_for(lambda: hub.connected and hass.states.get(SWITCH_1).state == "off", timeout=15)
    assert hass.states.get("binary_sensor.nikobus_connection").state == "on"
