"""Scenes, the motion detector, diagnostics, and a first setup."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.nikobus_ng.const import CONF_CONNECTION, DOMAIN
from custom_components.nikobus_ng.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from nikobus_connect.discovery import motion_detector_bus_addresses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    ENTRY_ID,
    KEY_SWITCH_1,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)

DETECTOR = "0986BC"


def _stored(hass_storage: dict[str, Any]) -> dict[str, Any]:
    return hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]


async def test_a_central_function_is_a_scene_that_sends_its_telegram(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    # The address of a key the simulator knows, so that its links are applied.
    _stored(hass_storage)["scenes"] = {
        KEY_SWITCH_1: {
            "bus_address": KEY_SWITCH_1,
            "pattern": "switch_pair",
            "outputs": [{"module_address": SWITCH, "channel": 1, "mode": "M05 (Impulse)"}],
        }
    }
    await setup()
    entity_id = er.async_get(hass).async_get_entity_id("scene", DOMAIN, f"{KEY_SWITCH_1}_scene")
    assert hass.states.get(entity_id).attributes["outputs"] == 1
    await hass.services.async_call("scene", "turn_on", {"entity_id": entity_id}, blocking=True)
    await wait_for(lambda: simulator.output(SWITCH, 1) == 0xFF)
    # The scene reads the groups it reached.
    await wait_for(lambda: hass.states.get("switch.switch_module_4707_output_1").state == "on", timeout=8)


async def test_a_motion_detector_gets_two_sensors_fed_by_its_frames(hass: HomeAssistant, hass_storage: dict[str, Any], simulator: WallSimulator, setup: SetupFixture) -> None:
    frames = motion_detector_bus_addresses(DETECTOR)
    _stored(hass_storage)["buttons"]["nikobus_button"][DETECTOR] = {
        "type": "Motion detector with Nikobus interface",
        "model": "05-7*5",
        "device_type": "28",
        "channels": 4,
        "description": "Motion detector with Nikobus interface #N0986BC",
        "operation_points": {label: {"bus_address": frame} for label, frame in zip(("1A", "1B", "1C", "1D"), (frames["on"], frames["off"], frames["light"], frames["dark"]), strict=True)},
    }
    await setup()
    registry = er.async_get(hass)
    occupancy = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DETECTOR}_occupancy")
    light = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DETECTOR}_light_level")
    assert hass.states.get(occupancy).state == "unknown"
    # The detector's keys are sensors, not event entities.
    assert registry.async_get_entity_id("event", DOMAIN, f"{frames['on']}_event") is None
    await simulator.wall_press(frames["on"])
    await wait_for(lambda: hass.states.get(occupancy).state == "on")
    await simulator.wall_press(frames["dark"])
    await wait_for(lambda: hass.states.get(light).state == "off")
    await simulator.wall_press(frames["off"])
    await wait_for(lambda: hass.states.get(occupancy).state == "off")


async def test_diagnostics_hold_the_installation_and_not_the_connection(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    dump = await async_get_config_entry_diagnostics(hass, entry)
    assert dump["entry"]["data"][CONF_CONNECTION] == "**REDACTED**"
    assert dump["gateway"] == {
        "connected": True,
        "answered_the_probe": True,
        "feedback_queries_seen": 0,
        "feedback_answers_seen": 0,
        "address": "86F5",
        "family": "pc_link",
        "feedback_module": False,
    }
    assert [(m["address"], m["type"], m["channels"]) for m in dump["modules"]] == [
        ("0E6C", "dimmer_module", 12),
        ("4707", "switch_module", 12),
        ("9105", "roller_module", 6),
    ]
    assert dump["keys"] == 20


async def test_a_first_setup_starts_a_discovery_by_itself(hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is known yet; the simulator has no inventory, so it ends in the error state."""
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_CLOSE_TIMEOUT", 0.2)
    del hass_storage[f"{DOMAIN}.{ENTRY_ID}"]
    assert await hass.config_entries.async_setup(entry.entry_id)
    hub = entry.runtime_data
    assert hub.discovery.running
    await hub.discovery.async_wait()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.nikobus_discovery").attributes["error"] == "no_pc_link"
    assert hass.states.get("binary_sensor.nikobus_connection").state == "on"
