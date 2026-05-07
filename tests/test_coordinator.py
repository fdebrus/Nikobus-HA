"""Tests for NikobusDataCoordinator — state buffer management and feedback processing."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nikobus_connect.discovery import InventoryQueryType

from custom_components.nikobus.coordinator import NikobusDataCoordinator


# ---------------------------------------------------------------------------
# Minimal coordinator-like object for testing pure state methods
# ---------------------------------------------------------------------------

class _FakeModuleStorage:
    """Duck-type NikobusModuleStorage with only the .data attribute."""

    def __init__(self, data: dict | None = None):
        self.data = data or {"nikobus_module": {}}


class _FakeCoord:
    """Duck-type a NikobusDataCoordinator with only the state buffer attributes."""

    def __init__(self, states: dict | None = None, module_data: dict | None = None):
        self.nikobus_module_states: dict = states or {}
        self.dict_module_data: dict = module_data or {}
        # ``module_storage.data`` is the 0.4.0 flat store. Build it from the
        # legacy nested ``module_data`` when the caller provides one, so
        # existing tests keep working without ceremony.
        flat: dict[str, dict] = {}
        for module_type, modules in (module_data or {}).items():
            if not isinstance(modules, dict):
                continue
            for addr, entry in modules.items():
                if not isinstance(entry, dict):
                    continue
                flat[str(addr).upper()] = {**entry, "module_type": module_type}
        self.module_storage = _FakeModuleStorage({"nikobus_module": flat})
        self.nikobus_command = None

    async def async_event_handler(self, event: str, data: dict) -> None:
        pass  # no-op for unit tests

    # Borrow the real implementation of each method under test
    get_bytearray_state = NikobusDataCoordinator.get_bytearray_state
    get_bytearray_group_state = NikobusDataCoordinator.get_bytearray_group_state
    set_bytearray_state = NikobusDataCoordinator.set_bytearray_state
    set_bytearray_group_state = NikobusDataCoordinator.set_bytearray_group_state
    _feedback_callback = NikobusDataCoordinator._feedback_callback
    get_cover_operation_time = NikobusDataCoordinator.get_cover_operation_time


def _coord(states=None, module_data=None):
    return _FakeCoord(states=states, module_data=module_data)


def _feedback_frame(addr_le: str, state_hex: str) -> str:
    """Build a minimal $1C-style frame string as received by process_feedback_data.

    Format: $1C<addr_le><00><state_12><CRC2>
    addr_le: 4 hex chars, little-endian address (e.g. "C7C1" for module "C1C7")
    state_hex: 12 hex chars
    """
    return f"$1C{addr_le}00{state_hex}FF"


# ---------------------------------------------------------------------------
# get_bytearray_state
# ---------------------------------------------------------------------------

class TestGetByteArrayState(unittest.TestCase):
    def test_returns_correct_byte(self):
        c = _coord(states={"C1C7": bytearray([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])})
        self.assertEqual(c.get_bytearray_state("C1C7", 1), 0x11)
        self.assertEqual(c.get_bytearray_state("C1C7", 3), 0x33)
        self.assertEqual(c.get_bytearray_state("C1C7", 6), 0x66)

    def test_unknown_address_returns_0(self):
        c = _coord()
        self.assertEqual(c.get_bytearray_state("FFFF", 1), 0)

    def test_channel_0_returns_0(self):
        c = _coord(states={"C1C7": bytearray([0xFF])})
        self.assertEqual(c.get_bytearray_state("C1C7", 0), 0)

    def test_channel_beyond_buffer_returns_0(self):
        c = _coord(states={"C1C7": bytearray(6)})
        self.assertEqual(c.get_bytearray_state("C1C7", 7), 0)

    def test_address_normalised_uppercase(self):
        c = _coord(states={"C1C7": bytearray([0xAB, 0x00])})
        self.assertEqual(c.get_bytearray_state("c1c7", 1), 0xAB)

    def test_switch_state_helper_on_off(self):
        c = _coord(states={"AABB": bytearray([0xFF, 0x00])})
        self.assertTrue(NikobusDataCoordinator.get_switch_state(c, "AABB", 1))
        self.assertFalse(NikobusDataCoordinator.get_switch_state(c, "AABB", 2))


# ---------------------------------------------------------------------------
# get_bytearray_group_state
# ---------------------------------------------------------------------------

class TestGetByteArrayGroupState(unittest.TestCase):
    def _12byte_coord(self):
        state = bytearray(range(12))  # 0..11
        return _coord(states={"C1C7": state})

    def test_group1_returns_first_6_bytes(self):
        c = self._12byte_coord()
        result = c.get_bytearray_group_state("C1C7", 1)
        self.assertEqual(result, bytearray(range(6)))

    def test_group2_returns_second_6_bytes(self):
        c = self._12byte_coord()
        result = c.get_bytearray_group_state("C1C7", 2)
        self.assertEqual(result, bytearray(range(6, 12)))

    def test_group2_on_6byte_module_returns_zeros(self):
        c = _coord(states={"C1C7": bytearray(6)})
        result = c.get_bytearray_group_state("C1C7", 2)
        self.assertEqual(result, bytearray(6))

    def test_unknown_address_returns_zeros(self):
        c = _coord()
        result = c.get_bytearray_group_state("FFFF", 1)
        self.assertEqual(result, bytearray(6))

    def test_string_group_coerced_to_int(self):
        c = self._12byte_coord()
        r1 = c.get_bytearray_group_state("C1C7", "1")
        r2 = c.get_bytearray_group_state("C1C7", 1)
        self.assertEqual(r1, r2)


# ---------------------------------------------------------------------------
# set_bytearray_state
# ---------------------------------------------------------------------------

class TestSetByteArrayState(unittest.TestCase):
    def test_sets_channel_value(self):
        c = _coord(states={"C1C7": bytearray(6)})
        c.set_bytearray_state("C1C7", 3, 0xAA)
        self.assertEqual(c.nikobus_module_states["C1C7"][2], 0xAA)

    def test_channel_1_maps_to_index_0(self):
        c = _coord(states={"C1C7": bytearray(6)})
        c.set_bytearray_state("C1C7", 1, 0xFF)
        self.assertEqual(c.nikobus_module_states["C1C7"][0], 0xFF)

    def test_unknown_address_does_not_raise(self):
        c = _coord()
        c.set_bytearray_state("FFFF", 1, 0xFF)  # silent no-op

    def test_channel_0_does_not_write(self):
        c = _coord(states={"C1C7": bytearray([0x11])})
        c.set_bytearray_state("C1C7", 0, 0xFF)
        self.assertEqual(c.nikobus_module_states["C1C7"][0], 0x11)

    def test_multiple_channels_independent(self):
        c = _coord(states={"C1C7": bytearray(6)})
        c.set_bytearray_state("C1C7", 1, 0x11)
        c.set_bytearray_state("C1C7", 2, 0x22)
        self.assertEqual(c.nikobus_module_states["C1C7"][0], 0x11)
        self.assertEqual(c.nikobus_module_states["C1C7"][1], 0x22)


# ---------------------------------------------------------------------------
# set_bytearray_group_state
# ---------------------------------------------------------------------------

class TestSetByteArrayGroupState(unittest.TestCase):
    def test_group1_updates_first_6_bytes(self):
        c = _coord(states={"C1C7": bytearray(12)})
        c.set_bytearray_group_state("C1C7", 1, "AABBCCDDEEFF")
        self.assertEqual(
            c.nikobus_module_states["C1C7"][:6],
            bytearray.fromhex("AABBCCDDEEFF"),
        )
        # Group 2 untouched
        self.assertEqual(c.nikobus_module_states["C1C7"][6:], bytearray(6))

    def test_group2_updates_second_6_bytes(self):
        c = _coord(states={"C1C7": bytearray(12)})
        c.set_bytearray_group_state("C1C7", 2, "112233445566")
        self.assertEqual(
            c.nikobus_module_states["C1C7"][6:],
            bytearray.fromhex("112233445566"),
        )
        # Group 1 untouched
        self.assertEqual(c.nikobus_module_states["C1C7"][:6], bytearray(6))

    def test_group2_on_6byte_module_ignored(self):
        c = _coord(states={"C1C7": bytearray(6)})
        c.set_bytearray_group_state("C1C7", 2, "AABBCCDDEEFF")
        self.assertEqual(c.nikobus_module_states["C1C7"], bytearray(6))

    def test_invalid_hex_ignored(self):
        c = _coord(states={"C1C7": bytearray(6)})
        original = bytes(c.nikobus_module_states["C1C7"])
        c.set_bytearray_group_state("C1C7", 1, "GGGGGGGGGGGG")
        self.assertEqual(bytes(c.nikobus_module_states["C1C7"]), original)

    def test_unknown_address_does_not_raise(self):
        c = _coord()
        c.set_bytearray_group_state("UNKNOWN", 1, "AABBCCDDEEFF")


# ---------------------------------------------------------------------------
# process_feedback_data
# ---------------------------------------------------------------------------

class TestFeedbackCallback(unittest.IsolatedAsyncioTestCase):
    async def test_group1_updates_first_6_bytes(self):
        c = _coord(states={"C1C7": bytearray(12)})
        frame = _feedback_frame("C7C1", "AABBCCDDEEFF")
        await c._feedback_callback(1, frame)
        self.assertEqual(
            c.nikobus_module_states["C1C7"][:6],
            bytearray.fromhex("AABBCCDDEEFF"),
        )

    async def test_group2_updates_second_6_bytes(self):
        c = _coord(states={"C1C7": bytearray(12)})
        frame = _feedback_frame("C7C1", "112233445566")
        await c._feedback_callback(2, frame)
        self.assertEqual(
            c.nikobus_module_states["C1C7"][6:],
            bytearray.fromhex("112233445566"),
        )

    async def test_group2_on_6byte_module_not_written(self):
        c = _coord(states={"C1C7": bytearray(6)})
        frame = _feedback_frame("C7C1", "AABBCCDDEEFF")
        await c._feedback_callback(2, frame)
        self.assertEqual(c.nikobus_module_states["C1C7"], bytearray(6))

    async def test_group1_does_not_touch_group2_bytes(self):
        state = bytearray([0] * 6 + [0xAA] * 6)
        c = _coord(states={"C1C7": state})
        frame = _feedback_frame("C7C1", "FFEEDDCCBBAA")
        await c._feedback_callback(1, frame)
        self.assertEqual(c.nikobus_module_states["C1C7"][6:], bytearray([0xAA] * 6))

    async def test_frame_too_short_ignored(self):
        c = _coord(states={"C1C7": bytearray(12)})
        await c._feedback_callback(1, "$1CC7C1")  # too short
        self.assertEqual(c.nikobus_module_states["C1C7"], bytearray(12))

    async def test_unknown_module_ignored(self):
        c = _coord(states={})
        frame = _feedback_frame("C7C1", "AABBCCDDEEFF")
        await c._feedback_callback(1, frame)  # should not raise

    async def test_resolve_pending_get_called_when_command_set(self):
        c = _coord(states={"C1C7": bytearray(12)})
        c.nikobus_command = MagicMock()
        c.nikobus_command.resolve_pending_get = MagicMock()

        frame = _feedback_frame("C7C1", "AABBCCDDEEFF")
        await c._feedback_callback(1, frame)

        c.nikobus_command.resolve_pending_get.assert_called_once_with(
            "C1C7", 1, "AABBCCDDEEFF"
        )

    async def test_address_decoded_correctly(self):
        """Address bytes in the frame are little-endian; confirm canonical form."""
        # Module address "AABB": little-endian in frame = "BBAA"
        c = _coord(states={"AABB": bytearray(12)})
        frame = _feedback_frame("BBAA", "001122334455")
        await c._feedback_callback(1, frame)
        self.assertEqual(
            c.nikobus_module_states["AABB"][:6],
            bytearray.fromhex("001122334455"),
        )


# ---------------------------------------------------------------------------
# get_cover_operation_time
# ---------------------------------------------------------------------------

class TestGetCoverOperationTime(unittest.TestCase):
    def _coord_with_cover(self, ch_data: dict):
        module_data = {
            "roller_module": {
                "C1C7": {
                    "channels": [ch_data],
                }
            }
        }
        return _coord(module_data=module_data)

    def test_returns_up_time(self):
        c = self._coord_with_cover({"operation_time_up": "25.5", "operation_time_down": "30"})
        result = c.get_cover_operation_time("C1C7", 1, "up")
        self.assertAlmostEqual(result, 25.5)

    def test_returns_down_time(self):
        c = self._coord_with_cover({"operation_time_up": "25.5", "operation_time_down": "30"})
        result = c.get_cover_operation_time("C1C7", 1, "down")
        self.assertAlmostEqual(result, 30.0)

    def test_missing_key_returns_default(self):
        c = self._coord_with_cover({})
        result = c.get_cover_operation_time("C1C7", 1, "up", default=15.0)
        self.assertAlmostEqual(result, 15.0)

    def test_zero_value_returns_default(self):
        c = self._coord_with_cover({"operation_time_up": "0"})
        result = c.get_cover_operation_time("C1C7", 1, "up", default=30.0)
        self.assertAlmostEqual(result, 30.0)

    def test_unknown_module_returns_default(self):
        c = _coord()
        result = c.get_cover_operation_time("FFFF", 1, "up", default=20.0)
        self.assertAlmostEqual(result, 20.0)

    def test_channel_out_of_range_returns_default(self):
        c = self._coord_with_cover({"operation_time_up": "25"})
        result = c.get_cover_operation_time("C1C7", 99, "up", default=30.0)
        self.assertAlmostEqual(result, 30.0)


# ---------------------------------------------------------------------------
# Discovery frame routing
# ---------------------------------------------------------------------------
#
# The previous HA-side "3 consecutive empty blocks" early-stop was removed
# when nikobus-connect 0.5.13 took over inventory termination: the library
# now drains its own queue on the first all-FF response (matching Niko's PC
# software). HA's job is reduced to forwarding every $2E/$1E frame to the
# library and updating UI progress. These tests pin that contract.

class TestDiscoveryFrameRouting(unittest.IsolatedAsyncioTestCase):
    """HA forwards inventory frames to the library and never short-circuits."""

    def _make_coordinator_stub(self):
        coord = MagicMock()
        coord.nikobus_discovery = MagicMock()
        coord.nikobus_discovery.parse_inventory_response = AsyncMock()
        coord.nikobus_discovery.parse_module_inventory_response = AsyncMock()
        coord.nikobus_discovery.discovered_devices = {}
        coord.discovery_running = True
        coord.inventory_query_type = InventoryQueryType.PC_LINK
        coord.discovery_registers_done = 0
        coord.discovery_registers_total = 96
        coord._update_discovery_state = MagicMock()
        coord.nikobus_command = MagicMock()
        coord.nikobus_command._command_queue = asyncio.Queue()

        coord._handle_discovery_finished = AsyncMock()
        coord._discovery_frame_callback = lambda msg, self_=coord: NikobusDataCoordinator._discovery_frame_callback(self_, msg)
        return coord

    async def test_all_ff_frame_forwarded_to_library_drain(self):
        """Library's drain (0.5.13+) runs in parse_inventory_response;
        HA forwards the frame and never drains itself."""
        coord = self._make_coordinator_stub()
        for i in range(5):
            coord.nikobus_command._command_queue.put_nowait({"command": f"$1410F586{i:02X}04"})

        # Simulate the library draining on first all-FF response.
        async def fake_parse(_message: str) -> None:
            while not coord.nikobus_command._command_queue.empty():
                coord.nikobus_command._command_queue.get_nowait()
        coord.nikobus_discovery.parse_inventory_response.side_effect = fake_parse

        all_ff = "$2EF586FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFCC98D0"
        await coord._discovery_frame_callback(all_ff)

        coord.nikobus_discovery.parse_inventory_response.assert_awaited_once_with(all_ff)
        self.assertEqual(coord.nikobus_command._command_queue.qsize(), 0)
        coord._handle_discovery_finished.assert_not_awaited()

    async def test_frame_forwarded_when_discovery_running(self):
        coord = self._make_coordinator_stub()
        data = "$2EF58603000000030000006C0E000001000000F938E8"

        await coord._discovery_frame_callback(data)

        coord.nikobus_discovery.parse_inventory_response.assert_awaited_once_with(data)
        self.assertEqual(coord.discovery_registers_done, 1)

    async def test_frame_dropped_when_discovery_not_running(self):
        coord = self._make_coordinator_stub()
        coord.discovery_running = False
        data = "$2EF58603000000030000006C0E000001000000F938E8"

        await coord._discovery_frame_callback(data)

        coord.nikobus_discovery.parse_inventory_response.assert_not_awaited()

    async def test_module_query_uses_module_parser(self):
        coord = self._make_coordinator_stub()
        coord.inventory_query_type = InventoryQueryType.MODULE
        empty = "$2EF586FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFCC98D0"

        for _ in range(5):
            await coord._discovery_frame_callback(empty)

        # Module queries route to a different parser and never invoke the
        # PC-Link inventory path.
        coord.nikobus_discovery.parse_module_inventory_response.assert_awaited()
        coord.nikobus_discovery.parse_inventory_response.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
