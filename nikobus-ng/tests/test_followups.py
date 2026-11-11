"""One module read again, what drives an output, and three things that go wrong quietly."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.nikobus_ng.const import CONF_FEEDBACK_MODULE, DOMAIN
from custom_components.nikobus_ng.diagnostics import async_get_config_entry_diagnostics
from custom_components.nikobus_ng.discovery import DiscoveryRunner
from custom_components.nikobus_ng.hub import NikobusHub
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import (
    DIMMER,
    ENTRY_ID,
    KEY_SCENE,
    KEY_SWITCH_1,
    ROLLER,
    SWITCH,
    SetupFixture,
    WallSimulator,
    wait_for,
)

GROUP = "38800A"


async def _reloaded(hass: HomeAssistant, entry: MockConfigEntry, hub: NikobusHub) -> NikobusHub:
    """The hub of the entry once it was reloaded and has read every group."""
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
    new_hub: NikobusHub = entry.runtime_data
    await wait_for(lambda: all(new_hub.value(a, 1 if g == 1 else 7) is not None for a, g in new_hub.inventory.output_groups()))
    await hass.async_block_till_done()
    return new_hub


async def test_the_links_of_one_module_can_be_read_again(hass: HomeAssistant, hass_storage: dict[str, Any], setup: SetupFixture, entry: MockConfigEntry) -> None:
    # A central function with an address of its own, on a module that is not read again.
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["scenes"] = {
        GROUP: {
            "bus_address": GROUP,
            "pattern": "roller_pair",
            "outputs": [
                {"module_address": ROLLER, "channel": channel, "mode": "M01 (Open - stop - close)"}
                for channel in (1, 2)
            ],
        }
    }
    hub = await setup()
    await hass.services.async_call(DOMAIN, "discover", {"address": SWITCH.lower()}, blocking=True)
    await hub.discovery.async_wait()
    inventory = (await _reloaded(hass, entry, hub)).inventory
    assert inventory.links_read(SWITCH)
    assert not inventory.links_read(DIMMER)
    assert inventory.impacted_groups(KEY_SWITCH_1) == [(SWITCH, 1)]
    # The light scene on a key of this module is found; the other module's function is kept whole.
    assert set(inventory.scenes) == {GROUP, KEY_SCENE}
    assert len(inventory.scenes[GROUP]["outputs"]) == 2


async def test_discover_refuses_a_module_the_inventory_does_not_hold(hass: HomeAssistant, setup: SetupFixture) -> None:
    await setup()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "discover", {"address": "FFFF"}, blocking=True)


async def test_an_output_names_the_keys_that_drive_it(hass: HomeAssistant, scanned: SetupFixture, entry: MockConfigEntry) -> None:
    await scanned()
    driven = hass.states.get("switch.switch_module_4707_output_1").attributes["controlled_by"]
    assert len(driven) == 1
    assert driven[0].endswith(f" 1C ({KEY_SWITCH_1})")
    assert hass.states.get("switch.switch_module_4707_output_2").attributes["controlled_by"] == []
    dump = await async_get_config_entry_diagnostics(hass, entry)
    links = dump["links"]
    assert links["modules"][SWITCH] == {"links": 4, "outputs_linked": 4, "outputs_unlinked": 8}
    # The twenty keys of the five plates; an audio module's triggers are not keys.
    # Five links, of which the dimmer's and the roller's are two-key modes:
    # the library files those on the key and on its partner.
    assert (links["keys_with_links"], links["keys_without_links"]) == (7, 13)
    assert links["modes"]["M14 (Light scene on)"] == 2


async def test_a_port_nothing_answers_on_is_a_repair_until_a_frame_arrives(hass: HomeAssistant, simulator: WallSimulator, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nikobus_connect.connection.PRESENCE_PROBE_TIMEOUT", 0.1)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ACK_WAIT_TIMEOUT", 0.6)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ATTEMPT_TIMEOUT", 0.2)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ANSWER_WAIT_TIMEOUT", 0.2)
    simulator.silent = True
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    issue_id = f"no_device_answered_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue.translation_placeholders == {"connection": simulator.connection_string}

    simulator.silent = False
    await simulator.wall_press(KEY_SWITCH_1, frames=1)
    await wait_for(lambda: ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None)
    await hass.async_block_till_done()


async def test_states_pushed_without_their_queries_are_a_repair_that_turns_the_option_off(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry, hass_client: ClientSessionGenerator) -> None:
    assert await async_setup_component(hass, "repairs", {})
    hass.config_entries.async_update_entry(entry, options={CONF_FEEDBACK_MODULE: True})
    hub = await setup()
    issue_id = f"feedback_module_port_{entry.entry_id}"
    for _ in range(5):
        await simulator.push_state(SWITCH)
    await wait_for(lambda: hub.listener.feedback_answers_seen == 5)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await simulator.push_state(SWITCH)
    await wait_for(lambda: ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None)

    client = await hass_client()
    response = await client.post("/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id})
    flow = await response.json()
    response = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await response.json())["type"] == "create_entry"
    new_hub = await _reloaded(hass, entry, hub)
    assert new_hub.feedback_module is False


async def test_a_link_table_the_library_could_not_read_is_a_repair(hass: HomeAssistant, scanned: SetupFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """The simulator stores no misaligned table, so the library's flag is set here.

    What is real: the flag is read before the library clears it, and a
    module read cleanly gets no notice.
    """
    finished = DiscoveryRunner.async_on_finished

    async def _flagged(self: DiscoveryRunner, **kwargs: Any) -> None:
        self._hub.engine.corrupt_link_tables.add(SWITCH)
        await finished(self, **kwargs)

    monkeypatch.setattr(DiscoveryRunner, "async_on_finished", _flagged)
    await scanned()
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"module_corrupt_links_{SWITCH.lower()}") is not None
    assert issues.async_get_issue(DOMAIN, f"module_corrupt_links_{DIMMER.lower()}") is None
