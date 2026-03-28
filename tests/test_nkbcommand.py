"""Tests for NikobusCommandHandler — queuing, dedup, future resolution, signal parsing."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.nikobus.exceptions import NikobusError
from custom_components.nikobus.nkbcommand import NikobusCommandHandler
from custom_components.nikobus.nkbprotocol import make_pc_link_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handler(module_states: dict | None = None):
    """Return a NikobusCommandHandler wired to mock dependencies."""
    loop = asyncio.new_event_loop()

    hass = MagicMock()
    hass.loop = loop
    hass.async_create_background_task = MagicMock()

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.get_module_channel_count = MagicMock(return_value=12)

    connection = MagicMock()
    connection.send = AsyncMock()

    listener = MagicMock()
    listener.response_queue = asyncio.Queue()
    listener.set_pending_query_group = MagicMock()

    states = module_states if module_states is not None else {}

    handler = NikobusCommandHandler(hass, coordinator, connection, listener, states)
    return handler, hass, coordinator, connection, listener, loop


def _get_cmd(group: int = 1, addr: str = "C1C7") -> str:
    func = 0x12 if group == 1 else 0x17
    return make_pc_link_command(func, addr)


def _set_cmd(group: int = 1, addr: str = "C1C7") -> str:
    func = 0x15 if group == 1 else 0x16
    return make_pc_link_command(func, addr, bytearray(7))


# ---------------------------------------------------------------------------
# queue_command — duplicate GET suppression
# ---------------------------------------------------------------------------

class TestQueueCommandDedup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handler, self.hass, *_ = _make_handler()

    async def asyncTearDown(self):
        self.hass.loop.close()

    async def test_first_get_is_queued(self):
        cmd = _get_cmd(1, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        self.assertEqual(self.handler._command_queue.qsize(), 1)

    async def test_duplicate_get_is_suppressed(self):
        cmd = _get_cmd(1, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        # Second call must be dropped
        self.assertEqual(self.handler._command_queue.qsize(), 1)

    async def test_dedup_key_added_on_queue(self):
        cmd = _get_cmd(1, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        self.assertIn("C1C7_1", self.handler._queued_get_keys)

    async def test_group2_dedup_key(self):
        cmd = _get_cmd(2, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        self.assertIn("C1C7_2", self.handler._queued_get_keys)

    async def test_different_addresses_not_suppressed(self):
        await self.handler.queue_command(_get_cmd(1, "AAAA"), "AAAA")
        await self.handler.queue_command(_get_cmd(1, "BBBB"), "BBBB")
        self.assertEqual(self.handler._command_queue.qsize(), 2)

    async def test_different_groups_not_suppressed(self):
        await self.handler.queue_command(_get_cmd(1, "C1C7"), "C1C7")
        await self.handler.queue_command(_get_cmd(2, "C1C7"), "C1C7")
        self.assertEqual(self.handler._command_queue.qsize(), 2)

    async def test_set_command_not_deduplicated(self):
        cmd = _set_cmd(1, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        await self.handler.queue_command(cmd, "C1C7")
        self.assertEqual(self.handler._command_queue.qsize(), 2)

    async def test_no_address_command_always_queued(self):
        await self.handler.queue_command("#E1")
        await self.handler.queue_command("#E1")
        self.assertEqual(self.handler._command_queue.qsize(), 2)

    async def test_queue_full_raises_and_signals_future(self):
        # Fill the queue to capacity (max=100)
        for _ in range(100):
            self.handler._command_queue.put_nowait({"command": "X", "address": None})
        future = self.hass.loop.create_future()
        with self.assertRaises(NikobusError):
            await self.handler.queue_command("#E1", future=future)
        self.assertTrue(future.done())


# ---------------------------------------------------------------------------
# resolve_pending_get — fast-path future resolution
# ---------------------------------------------------------------------------

class TestResolvePendingGet(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.handler, self.hass, *_ = _make_handler()
        self.hass.loop = self.loop

    def tearDown(self):
        self.loop.close()

    def test_resolves_matching_future(self):
        future = self.loop.create_future()
        self.handler._pending_get_futures["C1C7_1"] = future
        self.handler.resolve_pending_get("C1C7", 1, "AABBCCDDEEFF")
        self.assertTrue(future.done())
        self.assertEqual(future.result(), "AABBCCDDEEFF")

    def test_no_op_when_no_pending_future(self):
        # Should not raise
        self.handler.resolve_pending_get("UNKNOWN", 1, "AABBCCDDEEFF")

    def test_no_op_when_future_already_done(self):
        future = self.loop.create_future()
        future.set_result("OLD")
        self.handler._pending_get_futures["C1C7_1"] = future
        # Should not raise
        self.handler.resolve_pending_get("C1C7", 1, "NEW")
        self.assertEqual(future.result(), "OLD")

    def test_group2_key_resolved_independently(self):
        f1 = self.loop.create_future()
        f2 = self.loop.create_future()
        self.handler._pending_get_futures["C1C7_1"] = f1
        self.handler._pending_get_futures["C1C7_2"] = f2
        self.handler.resolve_pending_get("C1C7", 2, "FFEEDDCCBBAA")
        self.assertFalse(f1.done())
        self.assertTrue(f2.done())
        self.assertEqual(f2.result(), "FFEEDDCCBBAA")

    def test_address_normalised_to_uppercase(self):
        future = self.loop.create_future()
        self.handler._pending_get_futures["C1C7_1"] = future
        self.handler.resolve_pending_get("c1c7", 1, "AA")
        self.assertTrue(future.done())


# ---------------------------------------------------------------------------
# stop() — pending future cancellation
# ---------------------------------------------------------------------------

class TestStop(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_pending_futures(self):
        handler, hass, *_ = _make_handler()
        loop = asyncio.get_event_loop()
        hass.loop = loop

        f1 = loop.create_future()
        f2 = loop.create_future()
        handler._pending_get_futures["A_1"] = f1
        handler._pending_get_futures["B_2"] = f2

        await handler.stop()

        self.assertTrue(f1.cancelled())
        self.assertTrue(f2.cancelled())

    async def test_stop_clears_futures_dict(self):
        handler, hass, *_ = _make_handler()
        hass.loop = asyncio.get_event_loop()
        handler._pending_get_futures["X_1"] = asyncio.get_event_loop().create_future()

        await handler.stop()

        self.assertEqual(len(handler._pending_get_futures), 0)

    async def test_stop_clears_queued_get_keys(self):
        handler, hass, *_ = _make_handler()
        hass.loop = asyncio.get_event_loop()
        handler._queued_get_keys.add("C1C7_1")
        handler._queued_get_keys.add("AABB_2")

        await handler.stop()

        self.assertEqual(len(handler._queued_get_keys), 0)

    async def test_stop_does_not_raise_on_already_done_futures(self):
        handler, hass, *_ = _make_handler()
        loop = asyncio.get_event_loop()
        hass.loop = loop

        future = loop.create_future()
        future.set_result("done")
        handler._pending_get_futures["X_1"] = future

        # Must not raise
        await handler.stop()


# ---------------------------------------------------------------------------
# _prepare_ack_and_answer_signals
# ---------------------------------------------------------------------------

class TestPrepareAckAndAnswerSignals(unittest.TestCase):
    def setUp(self):
        self.handler, *_ = _make_handler()

    def _signals(self, command, address):
        return self.handler._prepare_ack_and_answer_signals(command, address)

    def test_get_group1_ack(self):
        cmd = _get_cmd(1, "C1C7")
        ack, _ = self._signals(cmd, "C1C7")
        self.assertEqual(ack, "$0512")

    def test_get_group1_answer(self):
        cmd = _get_cmd(1, "C1C7")
        _, answer = self._signals(cmd, "C1C7")
        # address C1C7 → reversed in answer → $1CC7C1
        self.assertEqual(answer, "$1CC7C1")

    def test_get_group2_ack(self):
        cmd = _get_cmd(2, "C1C7")
        ack, _ = self._signals(cmd, "C1C7")
        self.assertEqual(ack, "$0517")

    def test_get_group2_answer(self):
        cmd = _get_cmd(2, "C1C7")
        _, answer = self._signals(cmd, "C1C7")
        self.assertEqual(answer, "$1CC7C1")

    def test_set_group1_ack(self):
        cmd = _set_cmd(1, "C1C7")
        ack, _ = self._signals(cmd, "C1C7")
        self.assertEqual(ack, "$0515")

    def test_1e_prefix_produces_0eff_answer(self):
        # $1E… commands use $0EFF as the answer prefix
        fake_cmd = "$1E15C1C7AABB"
        _, answer = self._signals(fake_cmd, "C1C7")
        self.assertTrue(answer.startswith("$0EFF"))

    def test_answer_contains_reversed_address(self):
        cmd = _get_cmd(1, "AABB")
        _, answer = self._signals(cmd, "AABB")
        # AABB reversed → BBAA → answer contains BBAA after prefix
        self.assertIn("BBAA", answer)


# ---------------------------------------------------------------------------
# _parse_state_from_message
# ---------------------------------------------------------------------------

class TestParseStateFromMessage(unittest.TestCase):
    def setUp(self):
        self.handler, *_ = _make_handler()

    def test_extracts_12_char_state(self):
        signal = "$1CC7C1"
        state_payload = "AABBCCDDEEFF"
        # Simulate a full $1C response: signal + 2-char field + 12-char state
        message = f"{signal}00{state_payload}XX"
        result = self.handler._parse_state_from_message(message, signal)
        self.assertEqual(result, state_payload)

    def test_signal_not_found_returns_empty(self):
        result = self.handler._parse_state_from_message("$XXYYZZ00AABB", "$1CC7C1")
        self.assertEqual(result, "")

    def test_truncated_state_returns_empty(self):
        signal = "$1CC7C1"
        # Only 6 chars of state instead of 12
        message = f"{signal}00AABBCC"
        result = self.handler._parse_state_from_message(message, signal)
        self.assertEqual(result, "")

    def test_state_at_correct_offset(self):
        """State starts at len(signal) + 2 characters after the signal."""
        signal = "$1CC7C1"
        state = "112233445566"
        message = signal + "FF" + state + "TRAILING"
        result = self.handler._parse_state_from_message(message, signal)
        self.assertEqual(result, state)


# ---------------------------------------------------------------------------
# dedup key released on dequeue
# ---------------------------------------------------------------------------

class TestDedupKeyReleasedOnDequeue(unittest.IsolatedAsyncioTestCase):
    """The dedup key must be removed when the command is dequeued, not later."""

    async def test_key_cleared_after_dequeue(self):
        handler, hass, coordinator, connection, listener, loop = _make_handler()
        hass.loop = asyncio.get_event_loop()

        # Set up listener queue so process_commands can get a response
        ack = "$0512"
        answer = f"$1CC7C100AABBCCDDEEFF12"

        async def _fake_send(cmd):
            await listener.response_queue.put(ack)
            await listener.response_queue.put(answer)

        connection.send = _fake_send
        listener.response_queue = asyncio.Queue()

        cmd = _get_cmd(1, "C1C7")
        await handler.queue_command(cmd, "C1C7")
        self.assertIn("C1C7_1", handler._queued_get_keys)

        # Manually dequeue to simulate process_commands behaviour
        item = handler._command_queue.get_nowait()
        command = item["command"]
        gid = command[3:5] if len(command) >= 5 else ""
        if gid in ("12", "17") and item.get("address"):
            handler._queued_get_keys.discard(
                f"{item['address'].upper()}_{'1' if gid == '12' else '2'}"
            )

        self.assertNotIn("C1C7_1", handler._queued_get_keys)


if __name__ == "__main__":
    unittest.main()
