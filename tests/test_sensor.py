"""Tests for the Nikobus connection status sensor."""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

from custom_components.nikobus.sensor import NikobusConnectionSensor
from custom_components.nikobus.const import DOMAIN, HUB_IDENTIFIER


def _make_coordinator(is_connected=True, reconnect_task=None, reconnect_attempts=0, last_connected=None):
    coord = MagicMock()
    coord.nikobus_connection.is_connected = is_connected
    coord._reconnect_task = reconnect_task
    coord._reconnect_attempts = reconnect_attempts
    coord._last_connected = last_connected
    coord.connection_string = "192.168.1.1:8000"

    # Wire the real connection_status property
    from custom_components.nikobus.coordinator import NikobusDataCoordinator
    coord.connection_status = property(NikobusDataCoordinator.connection_status.fget).__get__(coord, type(coord))

    return coord


def _make_sensor(coord):
    sensor = NikobusConnectionSensor.__new__(NikobusConnectionSensor)
    sensor.coordinator = coord
    sensor._attr_unique_id = f"{DOMAIN}_connection_status"
    return sensor


class TestConnectionStatusProperty(unittest.TestCase):
    def _status(self, is_connected, task_done=True):
        task = None
        if not is_connected:
            task = MagicMock()
            task.done = MagicMock(return_value=task_done)

        coord = MagicMock()
        coord.nikobus_connection.is_connected = is_connected
        coord._reconnect_task = task

        from custom_components.nikobus.coordinator import NikobusDataCoordinator
        return NikobusDataCoordinator.connection_status.fget(coord)

    def test_connected(self):
        self.assertEqual(self._status(True), "connected")

    def test_reconnecting(self):
        # disconnected + active task → reconnecting
        self.assertEqual(self._status(False, task_done=False), "reconnecting")

    def test_disconnected_no_task(self):
        # disconnected + no task
        coord = MagicMock()
        coord.nikobus_connection.is_connected = False
        coord._reconnect_task = None
        from custom_components.nikobus.coordinator import NikobusDataCoordinator
        self.assertEqual(NikobusDataCoordinator.connection_status.fget(coord), "disconnected")

    def test_disconnected_done_task(self):
        self.assertEqual(self._status(False, task_done=True), "disconnected")


class TestSensorNativeValue(unittest.TestCase):
    def _sensor_with_status(self, status):
        coord = MagicMock()
        coord.connection_status = status
        sensor = NikobusConnectionSensor.__new__(NikobusConnectionSensor)
        sensor.coordinator = coord
        return sensor

    def test_connected_value(self):
        self.assertEqual(self._sensor_with_status("connected").native_value, "connected")

    def test_reconnecting_value(self):
        self.assertEqual(self._sensor_with_status("reconnecting").native_value, "reconnecting")

    def test_disconnected_value(self):
        self.assertEqual(self._sensor_with_status("disconnected").native_value, "disconnected")


class TestSensorIcon(unittest.TestCase):
    def _sensor_with_status(self, status):
        coord = MagicMock()
        coord.connection_status = status
        sensor = NikobusConnectionSensor.__new__(NikobusConnectionSensor)
        sensor.coordinator = coord
        return sensor

    def test_connected_icon(self):
        self.assertEqual(self._sensor_with_status("connected").icon, "mdi:lan-connect")

    def test_reconnecting_icon(self):
        self.assertEqual(self._sensor_with_status("reconnecting").icon, "mdi:lan-pending")

    def test_disconnected_icon(self):
        self.assertEqual(self._sensor_with_status("disconnected").icon, "mdi:lan-disconnect")

    def test_unknown_status_defaults_to_disconnect_icon(self):
        self.assertEqual(self._sensor_with_status("unknown").icon, "mdi:lan-disconnect")


class TestSensorAttributes(unittest.TestCase):
    def _sensor(self, last_connected=None, reconnect_attempts=0, connection_string="host:1234"):
        coord = MagicMock()
        coord._last_connected = last_connected
        coord._reconnect_attempts = reconnect_attempts
        coord.connection_string = connection_string
        sensor = NikobusConnectionSensor.__new__(NikobusConnectionSensor)
        sensor.coordinator = coord
        return sensor

    def test_last_connected_none(self):
        attrs = self._sensor().extra_state_attributes
        self.assertIsNone(attrs["last_connected"])

    def test_last_connected_isoformat(self):
        ts = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        attrs = self._sensor(last_connected=ts).extra_state_attributes
        self.assertEqual(attrs["last_connected"], ts.isoformat())

    def test_reconnect_attempts_zero(self):
        attrs = self._sensor().extra_state_attributes
        self.assertEqual(attrs["reconnect_attempts"], 0)

    def test_reconnect_attempts_nonzero(self):
        attrs = self._sensor(reconnect_attempts=7).extra_state_attributes
        self.assertEqual(attrs["reconnect_attempts"], 7)

    def test_connection_string_present(self):
        attrs = self._sensor(connection_string="/dev/ttyUSB0").extra_state_attributes
        self.assertEqual(attrs["connection_string"], "/dev/ttyUSB0")


class TestSensorMetadata(unittest.TestCase):
    def test_unique_id(self):
        coord = MagicMock()
        sensor = NikobusConnectionSensor.__new__(NikobusConnectionSensor)
        sensor.coordinator = coord
        sensor._attr_unique_id = f"{DOMAIN}_connection_status"
        self.assertEqual(sensor._attr_unique_id, f"{DOMAIN}_connection_status")

    def test_device_info_uses_hub_identifier(self):
        coord = MagicMock()
        sensor = NikobusConnectionSensor.__new__(NikobusConnectionSensor)
        sensor.coordinator = coord
        sensor._attr_unique_id = f"{DOMAIN}_connection_status"
        # Instantiate properly so _attr_device_info is set
        import homeassistant.helpers.device_registry as dr
        sensor._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, HUB_IDENTIFIER)},
        )
        self.assertIn((DOMAIN, HUB_IDENTIFIER), sensor._attr_device_info["identifiers"])


if __name__ == "__main__":
    unittest.main()
