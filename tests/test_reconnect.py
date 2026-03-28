"""Tests for auto-reconnect logic in NikobusDataCoordinator."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

from custom_components.nikobus.coordinator import NikobusDataCoordinator
from custom_components.nikobus.const import RECONNECT_DELAY_INITIAL, RECONNECT_DELAY_MAX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator():
    """Build a NikobusDataCoordinator with all external collaborators mocked."""
    hass = MagicMock()
    # async_create_background_task must actually schedule the coroutine so that
    # we can await it in tests.
    def _create_bg_task(coro, *, name=None):
        loop = asyncio.get_event_loop()
        return loop.create_task(coro)
    hass.async_create_background_task = _create_bg_task

    config_entry = MagicMock()
    config_entry.data = {
        "connection_string": "192.168.1.1:8000",
        "refresh_interval": 120,
        "has_feedbackmodule": False,
        "prior_gen3": False,
    }

    coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    # Minimal attribute init — bypass the real __init__ to avoid HA DataUpdateCoordinator
    coord.hass = hass
    coord.config_entry = config_entry
    coord.connection_string = "192.168.1.1:8000"
    coord._stopping = False
    coord._reconnect_task = None
    coord._reload_task = None
    coord._last_connected = None
    coord._reconnect_attempts = 0
    coord.discovery_running = False
    coord.dict_module_data = {}
    coord.nikobus_module_states = {}

    # Mock subsystems
    coord.nikobus_connection = MagicMock()
    coord.nikobus_connection.is_connected = False
    coord.nikobus_connection.connect = AsyncMock()
    coord.nikobus_connection.disconnect = AsyncMock()

    coord.nikobus_command = MagicMock()
    coord.nikobus_command.start = AsyncMock()
    coord.nikobus_command.stop = AsyncMock()
    coord.nikobus_command._command_queue = asyncio.Queue()

    coord.nikobus_listener = MagicMock()
    coord.nikobus_listener.start = AsyncMock()
    coord.nikobus_listener.stop = AsyncMock()
    coord.nikobus_listener.on_connection_lost = None

    coord.async_update_listeners = MagicMock()
    coord._async_update_data = AsyncMock()

    return coord


# ---------------------------------------------------------------------------
# _handle_connection_lost
# ---------------------------------------------------------------------------

class TestHandleConnectionLost(unittest.IsolatedAsyncioTestCase):
    async def test_no_op_when_stopping(self):
        coord = _make_coordinator()
        coord._stopping = True
        await coord._handle_connection_lost()
        coord.async_update_listeners.assert_not_called()
        coord.nikobus_command.stop.assert_not_called()

    async def test_marks_entities_unavailable(self):
        coord = _make_coordinator()
        await coord._handle_connection_lost()
        coord.async_update_listeners.assert_called_once()

    async def test_stops_command_handler(self):
        coord = _make_coordinator()
        await coord._handle_connection_lost()
        coord.nikobus_command.stop.assert_called_once()

    async def test_schedules_reconnect_task(self):
        coord = _make_coordinator()
        # Patch the reconnect loop so it completes immediately
        coord._reconnect_loop = AsyncMock()
        await coord._handle_connection_lost()
        # Wait briefly for the background task to be scheduled
        await asyncio.sleep(0)
        self.assertIsNotNone(coord._reconnect_task)

    async def test_does_not_double_schedule(self):
        """A second call while reconnect is in progress should not create a new task."""
        coord = _make_coordinator()

        # Simulate a long-running reconnect task
        async def _long_running():
            await asyncio.sleep(100)

        coord._reconnect_task = asyncio.create_task(_long_running())
        try:
            await coord._handle_connection_lost()
            await asyncio.sleep(0)
            # The existing task should not be replaced
            self.assertFalse(coord._reconnect_task.done())
        finally:
            coord._reconnect_task.cancel()
            try:
                await coord._reconnect_task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# _reconnect_loop
# ---------------------------------------------------------------------------

class TestReconnectLoop(unittest.IsolatedAsyncioTestCase):
    async def test_succeeds_on_first_attempt(self):
        coord = _make_coordinator()
        coord.nikobus_connection.connect = AsyncMock()  # succeeds immediately

        await coord._reconnect_loop()

        coord.nikobus_connection.connect.assert_called_once()
        coord.nikobus_command.start.assert_called_once()
        coord.nikobus_listener.start.assert_called_once()
        coord._async_update_data.assert_called_once()
        # Called once for "reconnecting" state, once for "connected" state after success.
        self.assertEqual(coord.async_update_listeners.call_count, 2)

    async def test_retries_after_connection_failure(self):
        coord = _make_coordinator()
        # Fail once, succeed on second attempt
        coord.nikobus_connection.connect = AsyncMock(
            side_effect=[Exception("refused"), None]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await coord._reconnect_loop()

        self.assertEqual(coord.nikobus_connection.connect.call_count, 2)
        mock_sleep.assert_called_once_with(RECONNECT_DELAY_INITIAL)

    async def test_backoff_doubles_each_retry(self):
        coord = _make_coordinator()
        # Fail twice, succeed on third
        coord.nikobus_connection.connect = AsyncMock(
            side_effect=[Exception("err"), Exception("err"), None]
        )

        sleep_calls = []
        async def _fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            await coord._reconnect_loop()

        self.assertEqual(sleep_calls[0], RECONNECT_DELAY_INITIAL)
        self.assertEqual(sleep_calls[1], RECONNECT_DELAY_INITIAL * 2)

    async def test_backoff_capped_at_max(self):
        coord = _make_coordinator()
        # Enough failures to hit the cap
        failures = [Exception("err")] * 10 + [None]
        coord.nikobus_connection.connect = AsyncMock(side_effect=failures)

        sleep_calls = []
        async def _fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            await coord._reconnect_loop()

        # All delays after the cap should equal RECONNECT_DELAY_MAX
        self.assertTrue(all(d <= RECONNECT_DELAY_MAX for d in sleep_calls))
        self.assertIn(RECONNECT_DELAY_MAX, sleep_calls)

    async def test_exits_immediately_when_stopping(self):
        coord = _make_coordinator()
        coord._stopping = True
        await coord._reconnect_loop()
        coord.nikobus_connection.connect.assert_not_called()

    async def test_exits_if_stopping_set_during_sleep(self):
        coord = _make_coordinator()
        coord.nikobus_connection.connect = AsyncMock(side_effect=Exception("err"))

        async def _set_stopping_and_raise(delay):
            coord._stopping = True
            # Simulate CancelledError that asyncio.sleep raises when task is cancelled
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=_set_stopping_and_raise):
            await coord._reconnect_loop()  # should return, not raise

        coord.nikobus_connection.connect.assert_called_once()

    async def test_drains_stale_commands_before_restart(self):
        coord = _make_coordinator()
        # Pre-load the queue with stale items
        coord.nikobus_command._command_queue.put_nowait({"cmd": "stale1"})
        coord.nikobus_command._command_queue.put_nowait({"cmd": "stale2"})

        await coord._reconnect_loop()

        self.assertTrue(coord.nikobus_command._command_queue.empty())

    async def test_registers_on_connection_lost_callback(self):
        """After reconnect, the callback must be re-armed on the listener."""
        coord = _make_coordinator()
        await coord._reconnect_loop()
        self.assertEqual(coord.nikobus_listener.on_connection_lost, coord._handle_connection_lost)

    async def test_disconnects_on_subsystem_start_failure(self):
        """If restarting subsystems fails, the connection is closed and we retry."""
        coord = _make_coordinator()
        coord.nikobus_command.start = AsyncMock(
            side_effect=[Exception("start failed"), None]
        )
        coord.nikobus_connection.connect = AsyncMock()

        sleep_calls = []
        async def _fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            await coord._reconnect_loop()

        coord.nikobus_connection.disconnect.assert_called_once()
        self.assertEqual(len(sleep_calls), 1)


# ---------------------------------------------------------------------------
# stop() — reconnect task cancellation
# ---------------------------------------------------------------------------

class TestStopCancelsReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_stop_sets_stopping_flag(self):
        coord = _make_coordinator()
        await coord.stop()
        self.assertTrue(coord._stopping)

    async def test_stop_cancels_reconnect_task(self):
        coord = _make_coordinator()

        async def _long_loop():
            await asyncio.sleep(100)

        task = asyncio.create_task(_long_loop())
        coord._reconnect_task = task
        await coord.stop()
        self.assertTrue(task.cancelled() or task.done())

    async def test_stop_calls_listener_and_command_stop(self):
        coord = _make_coordinator()
        await coord.stop()
        coord.nikobus_listener.stop.assert_called_once()
        coord.nikobus_command.stop.assert_called_once()

    async def test_stop_disconnects(self):
        coord = _make_coordinator()
        await coord.stop()
        coord.nikobus_connection.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# RECONNECT_DELAY constants sanity check
# ---------------------------------------------------------------------------

class TestReconnectConstants(unittest.TestCase):
    def test_initial_delay_positive(self):
        self.assertGreater(RECONNECT_DELAY_INITIAL, 0)

    def test_max_delay_ge_initial(self):
        self.assertGreaterEqual(RECONNECT_DELAY_MAX, RECONNECT_DELAY_INITIAL)


if __name__ == "__main__":
    unittest.main()
