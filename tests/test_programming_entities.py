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
        self.assertTrue(NikobusSyncClockButton(_coordinator()).available)
        self.assertFalse(NikobusSyncClockButton(_coordinator(running=True)).available)
        self.assertFalse(NikobusSyncClockButton(_coordinator(discovery_running=True)).available)

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
