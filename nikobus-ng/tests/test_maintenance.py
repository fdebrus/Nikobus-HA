"""The PC-Link's clock, the checks of the modules' programming, the backup.

The simulator answers a module's status and its memory, block by block.
It has no checksum function (0x13) and no clock, so those two answers
are given here: the checksum a module reports, and the time a PC-Link
keeps. Everything around them is the real code.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.nikobus_ng.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from nikobus_connect import NikobusAPI
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import ENTRY_ID, SWITCH, SetupFixture, wait_for

PC_LINK = "86F5"
PROGRAMMING = "sensor.nikobus_bridge_programming"
CLOCK = "sensor.nikobus_bridge_pc_link_clock"


@pytest.fixture
def backup_folder(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Keep the backup out of the shared configuration directory."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


async def test_a_memory_that_matches_its_checksum_is_ok(hass: HomeAssistant, setup: SetupFixture) -> None:
    hub = await setup()
    assert hass.states.get(PROGRAMMING).state == "unknown"
    # First with a checksum no memory has, to learn the one this memory gives.
    with patch.object(NikobusAPI, "get_module_crc", AsyncMock(return_value=0x1234)):
        report = await hass.services.async_call(
            DOMAIN, "verify_programming", {"modules": [SWITCH]}, blocking=True, return_response=True
        )
    check = report["modules"][SWITCH]
    assert (check["status"], check["checksum_ok"], check["image_bytes"]) == ("problem", False, 0x700)
    assert hass.states.get(PROGRAMMING).state == "problem"
    issue_id = f"module_checksum_mismatch_{SWITCH.lower()}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    with patch.object(NikobusAPI, "get_module_crc", AsyncMock(return_value=check["computed_checksum"])):
        report = await hass.services.async_call(
            DOMAIN, "verify_programming", {"modules": [SWITCH]}, blocking=True, return_response=True
        )
    assert report["modules"][SWITCH]["status"] == "ok"
    assert hass.states.get(PROGRAMMING).state == "ok"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    # A memory that checks out is the programming a later change is measured against.
    assert hub.inventory.programming[SWITCH]["checksum"] == check["computed_checksum"]


async def test_a_backup_writes_each_memory_and_a_summary(hass: HomeAssistant, setup: SetupFixture, backup_folder: Path) -> None:
    await setup()
    with patch.object(NikobusAPI, "get_module_crc", AsyncMock(return_value=0x1234)):
        report = await hass.services.async_call(
            DOMAIN, "backup_programming", {"modules": [SWITCH]}, blocking=True, return_response=True
        )
    folder = Path(report["path"])
    assert folder.parent == backup_folder / "nikobus_ng_backup"
    assert (folder / f"{SWITCH}_switch_module.nkm").stat().st_size == 0x700
    assert (folder / "summary.json").exists()
    assert hass.states.get(PROGRAMMING).attributes["backup"] == str(folder)


async def test_a_check_is_refused_while_a_discovery_runs(hass: HomeAssistant, setup: SetupFixture) -> None:
    hub = await setup()
    hub.discovery.running = True
    hub.notify()
    with pytest.raises(HomeAssistantError):
        await hub.maintenance.async_verify()
    assert hass.states.get("button.nikobus_bridge_check_programming").state == "unavailable"


async def test_a_moved_checksum_is_a_repair_that_a_discovery_settles(hass: HomeAssistant, setup: SetupFixture, hass_client: ClientSessionGenerator) -> None:
    assert await async_setup_component(hass, "repairs", {})
    hub = await setup()
    issue_id = f"module_reprogrammed_{SWITCH.lower()}"
    with patch.object(NikobusAPI, "get_module_crc", AsyncMock(return_value=0x1111)):
        assert await hub.maintenance.async_check_reprogrammed() == []
    assert hub.inventory.programming[SWITCH]["checksum"] == 0x1111
    with patch.object(NikobusAPI, "get_module_crc", AsyncMock(return_value=0x2222)):
        assert SWITCH in await hub.maintenance.async_check_reprogrammed()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).is_fixable
    assert SWITCH in hass.states.get(PROGRAMMING).attributes["reprogrammed"]

    client = await hass_client()
    response = await client.post("/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id})
    flow = await response.json()
    assert flow["step_id"] == "confirm"
    with patch.object(type(hub.discovery), "start") as start:
        response = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
        assert (await response.json())["type"] == "create_entry"
    start.assert_called_once()
    # The discovery's own end forgets the old checksums and their issues.
    hub.maintenance.forget_checksums()
    assert hub.inventory.programming == {}


async def test_the_clock_is_read_and_set_through_the_pc_link(hass: HomeAssistant, hass_storage: dict[str, Any], setup: SetupFixture) -> None:
    modules = hass_storage[f"{DOMAIN}.{ENTRY_ID}"]["data"]["modules"]["nikobus_module"]
    modules[PC_LINK] = {"module_type": "pc_link", "description": "PC-Link", "model": "05-200"}
    ahead = AsyncMock(side_effect=lambda _address: dt_util.now().replace(tzinfo=None) + timedelta(hours=1))
    with patch.object(NikobusAPI, "get_pc_link_time", ahead):
        await setup()
        await wait_for(lambda: hass.states.get(CLOCK).state not in ("unknown", "unavailable"))
        clock = hass.states.get(CLOCK)
        # The clock itself, an hour ahead; how far it is off is an attribute.
        assert abs((dt_util.parse_datetime(clock.state) - dt_util.utcnow()).total_seconds() - 3600) <= 5
        assert abs(clock.attributes["seconds_ahead"] - 3600) <= 2
        on_time = AsyncMock(side_effect=lambda _address: dt_util.now().replace(tzinfo=None))
        with (
            patch.object(NikobusAPI, "set_pc_link_time", AsyncMock()) as written,
            patch.object(NikobusAPI, "get_pc_link_time", on_time),
        ):
            await hass.services.async_call(
                "button", "press", {"entity_id": "button.nikobus_bridge_set_pc_link_clock"}, blocking=True
            )
    address, moment = written.await_args.args
    assert address == PC_LINK
    # Local wall time, without a zone: what the PC-Link keeps.
    assert isinstance(moment, datetime) and moment.tzinfo is None
    assert abs((moment - dt_util.now().replace(tzinfo=None)).total_seconds()) < 5
    assert abs(hass.states.get(CLOCK).attributes["seconds_ahead"]) <= 2


async def test_without_a_pc_link_there_is_no_clock(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    assert hass.states.get(CLOCK) is None
    assert hass.states.get("button.nikobus_bridge_set_pc_link_clock") is None


async def test_the_sensor_of_the_clocks_distance_is_removed(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    """Up to 0.7.0 the clock was a number of seconds; that entity would stay behind, unavailable."""
    registry = er.async_get(hass)
    former = registry.async_get_or_create("sensor", DOMAIN, f"{ENTRY_ID}_clock_drift", config_entry=entry)
    await setup()
    assert registry.async_get(former.entity_id) is None
