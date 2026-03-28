"""Tests for NikobusEventListener — frame extraction, CRC validation, dispatch routing."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.nikobus.const import CONF_HAS_FEEDBACK_MODULE
from custom_components.nikobus.nkblistener import NikobusEventListener
from custom_components.nikobus.nkbprotocol import make_pc_link_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_listener(has_feedback_module: bool = False, known_states: dict | None = None):
    """Instantiate a NikobusEventListener with fully mocked dependencies."""
    hass = MagicMock()
    hass.async_create_background_task = MagicMock()
    hass.async_create_task = MagicMock()

    config_entry = MagicMock()
    config_entry.data = {CONF_HAS_FEEDBACK_MODULE: has_feedback_module}

    coordinator = MagicMock()
    coordinator.discovery_running = False
    coordinator.nikobus_module_states = known_states if known_states is not None else {}

    actuator = MagicMock()
    actuator.handle_button_press = AsyncMock()

    connection = MagicMock()
    discovery = MagicMock()
    feedback_callback = AsyncMock()

    listener = NikobusEventListener(
        hass, config_entry, coordinator, actuator, connection, discovery, feedback_callback
    )
    return listener, feedback_callback, coordinator, actuator


def _valid_frame(func: int = 0x12, addr: str = "C1C7") -> str:
    """Return a CRC-valid bus frame produced by make_pc_link_command."""
    return make_pc_link_command(func, addr)


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

class TestExtractFrames(unittest.TestCase):
    def setUp(self):
        self.listener, *_ = _make_listener()

    def test_empty_input_returns_empty(self):
        self.assertEqual(self.listener._extract_frames(""), [])

    def test_no_delimiter_buffers_data(self):
        result = self.listener._extract_frames("$1234")
        self.assertEqual(result, [])
        self.assertEqual(self.listener._frame_buffer, "$1234")

    def test_single_complete_frame(self):
        result = self.listener._extract_frames("$1234ABCD\r")
        self.assertEqual(result, ["$1234ABCD"])
        self.assertEqual(self.listener._frame_buffer, "")

    def test_multiple_frames_in_one_read(self):
        result = self.listener._extract_frames("$AABB\r$CCDD\r")
        self.assertIn("$AABB", result)
        self.assertIn("$CCDD", result)
        self.assertEqual(len(result), 2)

    def test_partial_tail_buffered(self):
        result = self.listener._extract_frames("$AABB\r$partial")
        self.assertIn("$AABB", result)
        self.assertEqual(self.listener._frame_buffer, "$partial")

    def test_control_chars_stripped(self):
        result = self.listener._extract_frames("\x02$AABB\x03\r")
        self.assertEqual(result, ["$AABB"])

    def test_newline_treated_as_cr(self):
        # \n is converted to \r internally
        result = self.listener._extract_frames("$AABB\n")
        self.assertEqual(result, ["$AABB"])

    def test_collision_split_at_dollar(self):
        # Two frames concatenated without a \r between them
        result = self.listener._extract_frames("$AABB$CCDD\r")
        self.assertIn("$AABB", result)
        self.assertIn("$CCDD", result)

    def test_collision_split_at_hash(self):
        result = self.listener._extract_frames("$AABB#N1234\r")
        self.assertIn("$AABB", result)
        self.assertIn("#N1234", result)

    def test_buffer_accumulates_across_calls(self):
        self.listener._extract_frames("$12")
        result = self.listener._extract_frames("34\r")
        self.assertEqual(result, ["$1234"])


# ---------------------------------------------------------------------------
# CRC validation
# ---------------------------------------------------------------------------

class TestValidateCrc(unittest.TestCase):
    def setUp(self):
        self.listener, *_ = _make_listener()

    def test_ack_0515_is_valid(self):
        self.assertTrue(self.listener.validate_crc("$0515"))

    def test_ack_0517_is_valid(self):
        self.assertTrue(self.listener.validate_crc("$0517"))

    def test_ack_0516_is_valid(self):
        self.assertTrue(self.listener.validate_crc("$0516"))

    def test_command_frame_is_valid(self):
        cmd = _valid_frame(0x12, "C1C7")
        self.assertTrue(self.listener.validate_crc(cmd))

    def test_command_frame_group2_is_valid(self):
        cmd = _valid_frame(0x17, "C1C7")
        self.assertTrue(self.listener.validate_crc(cmd))

    def test_set_command_frame_is_valid(self):
        cmd = make_pc_link_command(0x15, "C1C7", bytearray(7))
        self.assertTrue(self.listener.validate_crc(cmd))

    def test_mangled_last_two_chars_fails(self):
        cmd = _valid_frame()
        mangled = cmd[:-2] + ("00" if cmd[-2:] != "00" else "FF")
        self.assertFalse(self.listener.validate_crc(mangled))

    def test_length_mismatch_fails(self):
        cmd = _valid_frame()
        # Append extra char so actual length no longer matches the field
        self.assertFalse(self.listener.validate_crc(cmd + "X"))

    def test_too_short_returns_false(self):
        self.assertFalse(self.listener.validate_crc("$1C"))

    def test_collision_prefix_skipped(self):
        # validate_crc strips everything before the last '$'
        cmd = _valid_frame()
        prefixed = "$JUNK" + cmd
        self.assertTrue(self.listener.validate_crc(prefixed))


# ---------------------------------------------------------------------------
# set_pending_query_group
# ---------------------------------------------------------------------------

class TestSetPendingQueryGroup(unittest.TestCase):
    def setUp(self):
        self.listener, *_ = _make_listener()

    def test_sets_group_1(self):
        self.listener.set_pending_query_group("C1C7", 1)
        self.assertEqual(self.listener._last_query_group["C1C7"], 1)

    def test_sets_group_2(self):
        self.listener.set_pending_query_group("C1C7", 2)
        self.assertEqual(self.listener._last_query_group["C1C7"], 2)

    def test_overwrites_previous(self):
        self.listener.set_pending_query_group("C1C7", 1)
        self.listener.set_pending_query_group("C1C7", 2)
        self.assertEqual(self.listener._last_query_group["C1C7"], 2)

    def test_multiple_addresses_independent(self):
        self.listener.set_pending_query_group("AAAA", 1)
        self.listener.set_pending_query_group("BBBB", 2)
        self.assertEqual(self.listener._last_query_group["AAAA"], 1)
        self.assertEqual(self.listener._last_query_group["BBBB"], 2)

    def test_empty_initially(self):
        self.assertEqual(self.listener._last_query_group, {})


# ---------------------------------------------------------------------------
# dispatch_message routing
# ---------------------------------------------------------------------------

class TestDispatchMessage(unittest.IsolatedAsyncioTestCase):
    """Verify that dispatch_message routes frames to the correct handlers."""

    def _make(self, has_feedback=False, known=None):
        return _make_listener(has_feedback_module=has_feedback, known_states=known)

    async def test_button_press_calls_handle_button_press(self):
        listener, _, _, actuator = self._make()
        await listener.dispatch_message("#N1234EE")
        actuator.handle_button_press.assert_awaited_once()

    async def test_button_press_not_enqueued(self):
        listener, _, _, _ = self._make()
        await listener.dispatch_message("#N1234EE")
        self.assertTrue(listener.response_queue.empty())

    async def test_ack_is_enqueued(self):
        listener, *_ = self._make()
        await listener.dispatch_message("$0515")
        self.assertFalse(listener.response_queue.empty())
        msg = listener.response_queue.get_nowait()
        self.assertEqual(msg, "$0515")

    async def test_get_echo_1012_discarded_without_feedback(self):
        listener, *_ = self._make(has_feedback=False)
        cmd = _valid_frame(0x12, "C1C7")
        await listener.dispatch_message(cmd)
        self.assertTrue(listener.response_queue.empty())

    async def test_get_echo_1017_discarded_without_feedback(self):
        listener, *_ = self._make(has_feedback=False)
        cmd = _valid_frame(0x17, "C1C7")
        await listener.dispatch_message(cmd)
        self.assertTrue(listener.response_queue.empty())

    async def test_get_echo_1012_updates_last_query_group_with_feedback(self):
        listener, *_ = self._make(has_feedback=True)
        cmd = _valid_frame(0x12, "C1C7")
        await listener.dispatch_message(cmd)
        # Should record group 1 for address C1C7
        self.assertEqual(listener._last_query_group.get("C1C7"), 1)

    async def test_get_echo_1017_updates_last_query_group_with_feedback(self):
        listener, *_ = self._make(has_feedback=True)
        cmd = _valid_frame(0x17, "C1C7")
        await listener.dispatch_message(cmd)
        self.assertEqual(listener._last_query_group.get("C1C7"), 2)

    async def test_get_echo_discarded_even_with_feedback(self):
        """$1012/$1017 echoes are never enqueued regardless of feedback setting."""
        listener, *_ = self._make(has_feedback=True)
        cmd = _valid_frame(0x12, "C1C7")
        await listener.dispatch_message(cmd)
        self.assertTrue(listener.response_queue.empty())

    async def test_feedback_1c_invalid_crc_discarded(self):
        listener, _, coordinator, _ = self._make(
            has_feedback=True, known={"C1C7": bytearray(12)}
        )
        coordinator.nikobus_module_states = {"C1C7": bytearray(12)}
        # Construct a $1C frame with known-bad CRC
        bad_frame = "$1CC7C100000000000000FFFF"  # CRC bytes "FF" are wrong
        await listener.dispatch_message(bad_frame)
        self.assertTrue(listener.response_queue.empty())

    async def test_discovery_frames_go_to_queue_when_discovery_running(self):
        listener, *_ = self._make()
        listener._coordinator.discovery_running = True
        # A random frame during discovery falls through to the queue
        await listener.dispatch_message("$XXYYZZ")
        self.assertFalse(listener.response_queue.empty())


# ---------------------------------------------------------------------------
# _last_query_group default behaviour
# ---------------------------------------------------------------------------

class TestLastQueryGroupDefault(unittest.TestCase):
    def test_default_is_1_when_not_set(self):
        listener, *_ = _make_listener(has_feedback_module=True)
        # get() with default 1 returns 1 for unknown address
        result = listener._last_query_group.get("UNKNOWN", 1)
        self.assertEqual(result, 1)

    def test_command_layer_registration_overrides_default(self):
        listener, *_ = _make_listener(has_feedback_module=True)
        listener.set_pending_query_group("MYADDR", 2)
        self.assertEqual(listener._last_query_group.get("MYADDR", 1), 2)


if __name__ == "__main__":
    unittest.main()
