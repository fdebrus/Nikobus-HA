"""Clock / programming-health sensors and the maintenance bridge buttons."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.nikobus.button import (
    NikobusBackupProgrammingButton,
    NikobusSyncClockButton,
    NikobusVerifyProgrammingButton,
)
from custom_components.nikobus.const import DOMAIN
from custom_components.nikobus.nkbprogramming import HEALTH_UNKNOWN
from custom_components.nikobus.sensor import (
    NikobusPcLinkClockSensor,
    NikobusProgrammingHealthSensor,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _coordinator(pc_link="86F5", running=False, discovery_running=False):
    programming = SimpleNamespace(
        pc_link_address=lambda: pc_link,
        clock=None,
        clock_read_at=None,
        clock_drift_seconds=None,
        running=running,
        checks={},
        last_check_at=None,
        last_backup_path=None,
        health=HEALTH_UNKNOWN,
        async_read_clock=AsyncMock(),
        async_sync_clock=AsyncMock(),
        async_verify_modules=AsyncMock(),
        async_backup_modules=AsyncMock(),
    )
    return SimpleNamespace(programming=programming, discovery_running=discovery_running)


class TestClockSensor(unittest.TestCase):
    def test_unavailable_without_pc_link(self):
        sensor = NikobusPcLinkClockSensor(_coordinator(pc_link=None))
        self.assertFalse(sensor.available)
        self.assertIsNone(sensor.native_value)

    def test_polls_the_controller(self):
        coord = _coordinator()
        sensor = NikobusPcLinkClockSensor(coord)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor._attr_unique_id, f"{DOMAIN}_pc_link_clock")
        _run(sensor.async_update())
        coord.programming.async_read_clock.assert_awaited_once()

    def test_skips_poll_while_maintenance_runs(self):
        coord = _coordinator(running=True)
        _run(NikobusPcLinkClockSensor(coord).async_update())
        coord.programming.async_read_clock.assert_not_awaited()


class TestHealthSensor(unittest.TestCase):
    def test_state_and_attributes(self):
        sensor = NikobusProgrammingHealthSensor.__new__(NikobusProgrammingHealthSensor)
        sensor.coordinator = _coordinator()
        self.assertEqual(sensor.native_value, HEALTH_UNKNOWN)
        self.assertEqual(sensor.extra_state_attributes["modules"], {})


class TestMaintenanceButtons(unittest.TestCase):
    def test_availability_gating(self):
        # One bridge action at a time: every bridge button greys out
        # while discovery or a maintenance run is busy.
        for cls in (
            NikobusVerifyProgrammingButton,
            NikobusSyncClockButton,
            NikobusBackupProgrammingButton,
                ):
            self.assertTrue(cls(_coordinator()).available, cls.__name__)
            self.assertFalse(cls(_coordinator(running=True)).available, cls.__name__)
            self.assertFalse(cls(_coordinator(discovery_running=True)).available, cls.__name__)

    def test_sync_button_awaits_sync(self):
        coord = _coordinator()
        _run(NikobusSyncClockButton(coord).async_press())
        coord.programming.async_sync_clock.assert_awaited_once()

    def test_verify_and_backup_run_in_background(self):
        coord = _coordinator()
        for cls, method in (
            (NikobusVerifyProgrammingButton, "async_verify_modules"),
            (NikobusBackupProgrammingButton, "async_backup_modules"),
        ):
            button = cls(coord)
            button.hass = MagicMock()
            _run(button.async_press())
            button.hass.async_create_background_task.assert_called_once()
            # close the un-awaited coroutine handed to the fake task runner
            button.hass.async_create_background_task.call_args.args[0].close()

    def test_press_refused_while_busy(self):
        button = NikobusVerifyProgrammingButton(_coordinator(running=True))
        button.hass = MagicMock()
        with self.assertRaises(Exception):  # noqa: B017 - stubbed HomeAssistantError
            _run(button.async_press())


if __name__ == "__main__":
    unittest.main()


class TestHubEntitiesSurviveOrphanCleanup(unittest.TestCase):
    """Every hub entity's unique_id must be in the coordinator's known set.

    3.15.0 shipped the maintenance entities without listing them, so the
    orphan cleanup deleted them right after the platforms added them —
    the buttons and sensors never appeared on the bridge device.
    """

    def test_maintenance_entity_ids_are_known(self):
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
        coord.dict_module_data = {}
        coord.dict_scene_data = {}
        coord.dict_button_data = {"nikobus_button": {}}
        coord.cf_storage = MagicMock()
        coord.cf_storage.data = {"nikobus_cf": {}}
        known = coord.get_known_entity_unique_ids()

        fake = _coordinator()
        entities = [
            NikobusPcLinkClockSensor(fake),
            NikobusSyncClockButton(fake),
            NikobusVerifyProgrammingButton(fake),
            NikobusBackupProgrammingButton(fake),
        ]
        health = NikobusProgrammingHealthSensor.__new__(NikobusProgrammingHealthSensor)
        health._attr_unique_id = f"{DOMAIN}_programming_health"
        entities.append(health)
        for entity in entities:
            self.assertIn(entity._attr_unique_id, known, entity.__class__.__name__)


class TestStatusFrameOutsideDiscovery(unittest.TestCase):
    """A $18 reply outside discovery (module status / CRC query) must not
    reach a discovery method — it used to call a method that no longer
    exists and error the listener loop on every status reply."""

    def test_inventory_callback_ignores_frame_when_idle(self):
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
        coord.nikobus_discovery = MagicMock(spec=[])  # no discovery methods at all
        _run(coord._inventory_callback("$18F58600500F3FFFAC61FE", False))


class TestStatusFrameDuringModuleScan(unittest.TestCase):
    """During the module stage a $18 frame is the module's reply to the
    engine's own status query. It must not start a discovery for the
    byte-swapped wire address it carries (phantom module, blocked queue)."""

    def test_module_stage_frame_does_not_start_a_scan(self):
        from custom_components.nikobus.coordinator import (
            InventoryQueryType,
            NikobusDataCoordinator,
        )

        coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
        coord.nikobus_discovery = MagicMock()
        coord.nikobus_discovery.query_module_inventory = AsyncMock()
        coord.inventory_query_type = InventoryQueryType.MODULE
        _run(coord._inventory_callback("$18F58600500C3FFFF531D1", True))
        coord.nikobus_discovery.query_module_inventory.assert_not_awaited()
        coord.nikobus_discovery.handle_device_address_inventory.assert_not_called()

    def test_pc_link_stage_frame_still_feeds_the_inventory(self):
        from custom_components.nikobus.coordinator import (
            InventoryQueryType,
            NikobusDataCoordinator,
        )

        coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
        coord.nikobus_discovery = MagicMock()
        coord.inventory_query_type = InventoryQueryType.PC_LINK
        _run(coord._inventory_callback("$18F58600500F3FFFAC61FE", True))
        coord.nikobus_discovery.handle_device_address_inventory.assert_called_once()
