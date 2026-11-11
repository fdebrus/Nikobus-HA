"""A discovery: the link pass against the simulator, and the ways it stops."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.nikobus_ng.const import DOMAIN
from custom_components.nikobus_ng.discovery import DiscoveryRunner
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from nikobus_connect.discovery import InventoryQueryType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    DIMMER,
    ENTRY_ID,
    KEY_DIMMER,
    KEY_ROLLER,
    KEY_SCENE,
    KEY_SWITCH_1,
    ROLLER,
    SWITCH,
    SetupFixture,
    wait_for,
)


async def test_the_link_pass_files_every_link_and_reloads(hass: HomeAssistant, scanned: SetupFixture) -> None:
    hub = await scanned()
    inventory = hub.inventory
    assert all(inventory.links_read(address) for address in (SWITCH, DIMMER, ROLLER))
    assert inventory.impacted_groups(KEY_SWITCH_1) == [(SWITCH, 1)]
    assert inventory.impacted_groups(KEY_DIMMER) == [(DIMMER, 1)]
    assert inventory.impacted_groups(KEY_ROLLER) == [(ROLLER, 1)]
    assert hub.discovery.status.phase == "idle"
    # Both groups of the switch module hold untimed links only.
    assert hub.command.steady_groups == frozenset({(SWITCH, 1), (SWITCH, 2)})


async def test_a_light_scene_the_scan_finds_becomes_a_scene(hass: HomeAssistant, scanned: SetupFixture) -> None:
    """Through the real scan: the library clears what it classified once the pass is reported."""
    hub = await scanned()
    assert list(hub.inventory.scenes) == [KEY_SCENE]
    entity_id = er.async_get(hass).async_get_entity_id("scene", DOMAIN, f"{KEY_SCENE}_scene")
    assert hass.states.get(entity_id).attributes["outputs"] == 2


async def test_an_output_no_key_drives_is_created_disabled_after_a_scan(hass: HomeAssistant, hass_storage: dict, entry: MockConfigEntry, setup: SetupFixture) -> None:
    """Decided when the entity is first created: a module whose links were all read."""
    modules = hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["modules"]["nikobus_module"]
    modules[SWITCH]["links_read_at"] = "2026-11-11T10:00:00+00:00"
    await setup()
    registry = er.async_get(hass)
    unlinked = registry.async_get(registry.async_get_entity_id("switch", DOMAIN, f"{SWITCH}_3"))
    assert unlinked.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # A module not scanned yet keeps every output enabled.
    dimmer = registry.async_get(registry.async_get_entity_id("light", DOMAIN, f"{DIMMER}_3"))
    assert dimmer.disabled_by is None


async def test_a_discovery_is_refused_while_one_runs(hass: HomeAssistant, setup: SetupFixture) -> None:
    hub = await setup()
    hub.discovery.running = True
    with pytest.raises(HomeAssistantError):
        hub.discovery.start()
    assert hass.states.get("button.nikobus_discover_installation").state == "unknown"


async def test_no_pc_link_ends_in_an_error_the_sensor_shows(hass: HomeAssistant, setup: SetupFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """The simulator serves no inventory, like a bus without a PC-Link."""
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr("custom_components.nikobus_ng.discovery.INVENTORY_CLOSE_TIMEOUT", 0.2)
    hub = await setup()
    await hass.services.async_call("button", "press", {"entity_id": "button.nikobus_discover_installation"}, blocking=True)
    await hub.discovery.async_wait()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.nikobus_discovery")
    assert (state.state, state.attributes["error"]) == ("error", "no_pc_link")
    assert not hub.discovery.running
    await wait_for(lambda: hass.states.get("button.nikobus_discover_installation").state != "unavailable")


@pytest.mark.parametrize(
    ("query_type", "inventory_calls", "module_calls"),
    [
        pytest.param(InventoryQueryType.PC_LINK, 1, 0, id="inventory-pass"),
        pytest.param(InventoryQueryType.MODULE, 0, 1, id="link-pass"),
        pytest.param(None, 0, 0, id="between-passes"),
    ],
)
async def test_memory_blocks_go_to_the_pass_that_asked(query_type: InventoryQueryType | None, inventory_calls: int, module_calls: int) -> None:
    hub = MagicMock()
    hub.inventory_query_type = query_type
    hub.engine.parse_inventory_response = AsyncMock()
    hub.engine.parse_module_inventory_response = AsyncMock()
    runner = DiscoveryRunner(hub)
    runner.running = True
    await runner.async_on_answer_frame("$2E0000")
    assert hub.engine.parse_inventory_response.await_count == inventory_calls
    assert hub.engine.parse_module_inventory_response.await_count == module_calls
    assert runner._first_answer.is_set() is bool(inventory_calls)


async def test_frames_outside_a_discovery_are_ignored() -> None:
    hub = MagicMock()
    hub.inventory_query_type = InventoryQueryType.PC_LINK
    hub.engine.parse_inventory_response = AsyncMock()
    runner = DiscoveryRunner(hub)
    await runner.async_on_answer_frame("$2E0000")
    runner.on_inventory_frame("$180000")
    hub.engine.parse_inventory_response.assert_not_awaited()
    hub.engine.handle_device_address_inventory.assert_not_called()
