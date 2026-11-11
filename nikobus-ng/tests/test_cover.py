"""A shutter: the motor through the simulator, the position through a clock."""

from __future__ import annotations

from datetime import timedelta

from custom_components.nikobus_ng.cover import NikobusCover
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from nikobus_connect.travel import TravelCalculator
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import ROLLER, SetupFixture, WallSimulator, wait_for

COVER = "cover.roller_shutter_module_9105_output_1"


class _Clock:
    """A clock the test moves; the travel model reads it instead of the real one."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _with_clock(hass: HomeAssistant, run_time: float = 10.0) -> tuple[NikobusCover, _Clock]:
    entity = hass.data["domain_entities"]["cover"][COVER]
    clock = _Clock()
    position = entity._travel.current_position()
    entity._travel = TravelCalculator(run_time, run_time, clock=clock)
    entity._travel.set_position(position)
    return entity, clock


async def _tick(hass: HomeAssistant, entity: NikobusCover) -> None:
    await entity._async_tick(dt_util.utcnow())
    await hass.async_block_till_done()


async def test_open_and_close_drive_the_motor(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    await hass.services.async_call("cover", "close_cover", {"entity_id": COVER}, blocking=True)
    assert simulator.output(ROLLER, 1) == 0x02
    assert hass.states.get(COVER).state == "closing"
    await hass.services.async_call("cover", "stop_cover", {"entity_id": COVER}, blocking=True)
    assert simulator.output(ROLLER, 1) == 0x00
    assert hass.states.get(COVER).state in ("open", "closed")


async def test_a_position_is_reached_and_the_motor_stopped_there(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    await setup()
    entity, clock = _with_clock(hass)
    assert hass.states.get(COVER).attributes["current_position"] == 100
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": COVER, "position": 40}, blocking=True)
    assert simulator.output(ROLLER, 1) == 0x02
    clock.now += 3
    await _tick(hass, entity)
    assert hass.states.get(COVER).attributes["current_position"] == 70
    assert simulator.output(ROLLER, 1) == 0x02
    clock.now += 3
    await _tick(hass, entity)
    await wait_for(lambda: simulator.output(ROLLER, 1) == 0x00)
    state = hass.states.get(COVER)
    assert (state.state, state.attributes["current_position"]) == ("open", 40)


async def test_a_full_travel_runs_past_the_estimate_before_the_relay_is_released(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.nikobus_ng.cover.COVER_END_MARGIN", 0.0)
    await setup()
    entity, clock = _with_clock(hass)
    await hass.services.async_call("cover", "close_cover", {"entity_id": COVER}, blocking=True)
    clock.now += 11
    await _tick(hass, entity)  # at the end: the margin starts
    assert simulator.output(ROLLER, 1) == 0x02
    await _tick(hass, entity)  # the margin is over
    await wait_for(lambda: simulator.output(ROLLER, 1) == 0x00)
    assert hass.states.get(COVER).state == "closed"


async def test_a_move_started_at_the_wall_is_followed(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    hub = await setup()
    _entity, clock = _with_clock(hass)
    simulator.gateway.modules[ROLLER].outputs[0] = 0x02  # the wall key started it
    await hub.async_read_group(ROLLER, 1)
    assert hass.states.get(COVER).state == "closing"
    clock.now += 5
    simulator.gateway.modules[ROLLER].outputs[0] = 0x00  # and stopped it
    await hub.async_read_group(ROLLER, 1)
    state = hass.states.get(COVER)
    assert (state.state, state.attributes["current_position"]) == ("open", 50)
    # Nothing was written: the move was not ours to end.
    assert simulator.output(ROLLER, 1) == 0x00
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
