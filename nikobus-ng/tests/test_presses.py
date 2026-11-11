"""A key pressed at the wall: its event, and the read of what it drives."""

from __future__ import annotations

from custom_components.nikobus_ng.const import DOMAIN, EVENT_PRESS
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    KEY_SWITCH_1,
    KEY_SWITCH_8,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)


def _event_entity(hass: HomeAssistant, bus_address: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("event", DOMAIN, f"{bus_address}_event")
    assert entity_id is not None
    return entity_id


async def test_a_press_fires_the_keys_event_entity(hass: HomeAssistant, simulator: WallSimulator, scanned: SetupFixture) -> None:
    await scanned()
    entity_id = _event_entity(hass, KEY_SWITCH_1)
    types: list[str] = []
    hass.bus.async_listen(EVENT_PRESS, lambda event: types.append(event.data["type"]))
    await simulator.wall_press(KEY_SWITCH_1)
    await wait_for(lambda: "short_release" in types)
    assert types == ["pressed", "short_release"]
    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == "short_release"
    assert state.attributes["bus_address"] == KEY_SWITCH_1


async def test_the_output_a_key_drives_is_read_after_the_press(hass: HomeAssistant, simulator: WallSimulator, scanned: SetupFixture) -> None:
    await scanned()
    assert hass.states.get("switch.switch_module_4707_output_1").state == "off"
    await simulator.wall_press(KEY_SWITCH_1)  # an impulse link on output 1
    await wait_for(lambda: hass.states.get("switch.switch_module_4707_output_1").state == "on")
    await simulator.wall_press(KEY_SWITCH_8)  # output 8, in the second group
    await wait_for(lambda: hass.states.get("switch.switch_module_4707_output_8").state == "on")


async def test_a_press_makes_the_next_write_read_first(hass: HomeAssistant, simulator: WallSimulator, scanned: SetupFixture) -> None:
    """The steady group is trusted until a press reaches it."""
    hub = await scanned()
    assert (SWITCH, 1) in hub.command.steady_groups
    assert hub.command.group_state_age(SWITCH, 1) is not None
    hub.presses.frame(KEY_SWITCH_1)
    assert hub.command.group_state_age(SWITCH, 1) is None
    assert hub.command.group_state_age(SWITCH, 2) is not None


async def test_a_key_nobody_knows_still_fires_the_bus_event_and_forgets_everything(hass: HomeAssistant, simulator: WallSimulator, scanned: SetupFixture) -> None:
    hub = await scanned()
    seen: list[Event] = []
    hass.bus.async_listen(EVENT_PRESS, seen.append)
    await simulator.wall_press("123456")
    await wait_for(lambda: len(seen) >= 1)
    assert seen[0].data == {"address": "123456", "type": "pressed"}
    assert hub.command.group_state_age(SWITCH, 1) is None
    assert hub.command.group_state_age(SWITCH, 2) is None


async def test_a_press_from_home_assistant_reaches_the_module(hass: HomeAssistant, simulator: WallSimulator, scanned: SetupFixture) -> None:
    await scanned()
    registry = er.async_get(hass)
    press = registry.async_get_entity_id("button", DOMAIN, f"{KEY_SWITCH_1}_press")
    registry.async_update_entity(press, disabled_by=None)
    await hass.config_entries.async_reload(registry.async_get(press).config_entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": press}, blocking=True)
    await wait_for(lambda: simulator.output(SWITCH, 1) == 0xFF)
    # The gateway does not relay the host's own press: the state comes from the read.
    await wait_for(lambda: hass.states.get("switch.switch_module_4707_output_1").state == "on")
