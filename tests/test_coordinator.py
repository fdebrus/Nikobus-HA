"""Tests for NikobusDataCoordinator — state buffer management and feedback processing."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.discovery import InventoryQueryType

from custom_components.nikobus.const import (
    DISCOVERY_SUB_PHASE_IDENTITY,
    DISCOVERY_SUB_PHASE_INVENTORY,
)
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
        # The real coordinator stores the state buffer in ``_module_states``
        # and exposes it through the ``nikobus_module_states`` read-only
        # property. The borrowed methods reach for ``self._module_states``
        # directly, so we set that as the source of truth here and alias
        # ``nikobus_module_states`` to the same dict so assertions reading
        # either name see the same data.
        self._module_states: dict = states or {}
        self.nikobus_module_states = self._module_states
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
        # ``set_bytearray_state`` and ``get_bytearray_group_state`` on
        # the real coordinator delegate to ``self.nikobus_command``
        # (the library's command handler), which holds the canonical
        # state buffer. Wire a real handler here, backed by the same
        # ``_module_states`` dict, so the delegated path works in
        # tests without needing a connection or listener.
        self.nikobus_command = NikobusCommandHandler(
            connection=MagicMock(),
            listener=MagicMock(),
            module_states=self._module_states,
        )

    async def async_event_handler(self, event: str, data: dict) -> None:
        pass  # no-op for unit tests

    # Borrow the real implementation of each method under test
    get_bytearray_state = NikobusDataCoordinator.get_bytearray_state
    get_bytearray_group_state = NikobusDataCoordinator.get_bytearray_group_state
    set_bytearray_state = NikobusDataCoordinator.set_bytearray_state
    set_bytearray_group_state = NikobusDataCoordinator.set_bytearray_group_state
    _feedback_callback = NikobusDataCoordinator._feedback_callback
    get_cover_operation_time = NikobusDataCoordinator.get_cover_operation_time
    _refresh_module_type = NikobusDataCoordinator._refresh_module_type


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

    def test_unknown_address_autovivifies_buffer(self):
        # A single-channel write to an unknown address allocates a fresh
        # 12-byte buffer (optimistic write) — it is NOT a silent no-op.
        c = _coord()
        c.set_bytearray_state("FFFF", 1, 0xFF)
        self.assertEqual(c.nikobus_module_states["FFFF"][0], 0xFF)

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

    def test_unknown_address_is_a_no_op(self):
        # Unlike set_bytearray_state, the group write does NOT allocate a
        # buffer for an unknown module — it leaves the store untouched.
        c = _coord()
        c.set_bytearray_group_state("UNKNOWN", 1, "AABBCCDDEEFF")
        self.assertNotIn("UNKNOWN", c.nikobus_module_states)


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

    async def test_unknown_module_auto_allocated(self):
        # Feedback is authoritative: a frame from a not-yet-known module
        # auto-allocates its buffer and records the state (the callback's
        # "Auto-allocate if module wasn't pre-registered" path) — it is
        # not ignored.
        c = _coord(states={})
        frame = _feedback_frame("C7C1", "AABBCCDDEEFF")
        await c._feedback_callback(1, frame)
        self.assertEqual(
            c.nikobus_module_states["C1C7"][:6], bytearray.fromhex("AABBCCDDEEFF")
        )

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
        coord.discovery_sub_phase = DISCOVERY_SUB_PHASE_INVENTORY
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

    async def test_inventory_message_written_only_during_inventory_sub_phase(self):
        """2.11.2: ``parse_inventory_response`` receives BOTH the PC-Link
        broadcast frames during inventory AND the per-register ``$2E``
        responses during the library's identity phase (96 reads × N
        modules). Writing the "PC Link inventory: X/Y" message
        unconditionally — as pre-2.11.2 did — overwrites the
        "Identifying modules…" message ``_handle_discovery_progress``
        sets when the library transitions to identity, freezing the
        UI in apparent-inventory state long after discovery has moved on.

        Gate: only write the inventory-style message while
        ``discovery_sub_phase == DISCOVERY_SUB_PHASE_INVENTORY``.
        """
        coord = self._make_coordinator_stub()
        coord.discovery_sub_phase = DISCOVERY_SUB_PHASE_IDENTITY
        data = "$2EF58603000000030000006C0E000001000000F938E8"

        await coord._discovery_frame_callback(data)

        # Frame still counted toward the register progress bar
        # (parse_inventory_response is the per-frame increment hook).
        coord.nikobus_discovery.parse_inventory_response.assert_awaited_once_with(data)
        self.assertEqual(coord.discovery_registers_done, 1)
        # But the status message must NOT be touched — keeps whatever
        # _handle_discovery_progress wrote for the identity phase.
        coord._update_discovery_state.assert_not_called()

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


# ---------------------------------------------------------------------------
# Stale-inventory purge (companion to the library's detect_stale_inventory)
# ---------------------------------------------------------------------------

class TestPurgeInventoryAddresses(unittest.IsolatedAsyncioTestCase):
    """Verify the storage write path that consumes detect_stale_inventory()."""

    def _make_coordinator_stub(self):
        coord = MagicMock()
        coord.module_storage = MagicMock()
        coord.module_storage.data = {
            "nikobus_module": {
                "AABB": {"module_type": "switch_module", "model": "05-002-02"},
                "CCDD": {"module_type": "dimmer_module", "model": "05-007-02"},
            }
        }
        coord.module_storage.async_save = AsyncMock()
        coord.button_storage = MagicMock()
        coord.button_storage.async_save = AsyncMock()
        coord.dict_button_data = {
            "nikobus_button": {
                "AABBCC": {"type": "Button", "operation_points": {}},
                "DDEEFF": {"type": "Button", "operation_points": {}},
            }
        }
        coord._rebuild_dict_module_data = MagicMock()

        coord.config_entry = MagicMock()
        coord.config_entry.entry_id = "entry_test"
        coord.hass = MagicMock()
        coord.hass.data = {}
        coord.hass.config_entries.async_reload = MagicMock()
        coord.hass.async_create_task = MagicMock()

        coord.purge_inventory_addresses = lambda addrs, self_=coord: \
            NikobusDataCoordinator.purge_inventory_addresses(self_, addrs)
        return coord

    async def test_removes_module_address(self):
        coord = self._make_coordinator_stub()
        result = await coord.purge_inventory_addresses(["AABB"])

        self.assertEqual(result["removed_modules"], ["AABB"])
        self.assertEqual(result["removed_buttons"], [])
        self.assertEqual(result["not_found"], [])
        self.assertNotIn("AABB", coord.module_storage.data["nikobus_module"])
        # The other module is untouched.
        self.assertIn("CCDD", coord.module_storage.data["nikobus_module"])
        coord.module_storage.async_save.assert_awaited_once()
        coord.button_storage.async_save.assert_awaited_once()
        coord._rebuild_dict_module_data.assert_called_once()
        coord.hass.async_create_task.assert_called_once()

    async def test_removes_button_address(self):
        coord = self._make_coordinator_stub()
        result = await coord.purge_inventory_addresses(["DDEEFF"])

        self.assertEqual(result["removed_buttons"], ["DDEEFF"])
        self.assertEqual(result["removed_modules"], [])
        self.assertNotIn("DDEEFF", coord.dict_button_data["nikobus_button"])
        coord.hass.async_create_task.assert_called_once()

    async def test_normalises_case_and_whitespace(self):
        coord = self._make_coordinator_stub()
        result = await coord.purge_inventory_addresses(["  aabb  ", "ddeeff"])

        # Both inputs match after upper-case + strip.
        self.assertEqual(set(result["removed_modules"]), {"AABB"})
        self.assertEqual(set(result["removed_buttons"]), {"DDEEFF"})

    async def test_unknown_address_reported_as_not_found(self):
        coord = self._make_coordinator_stub()
        result = await coord.purge_inventory_addresses(["AABB", "9999"])

        self.assertEqual(result["removed_modules"], ["AABB"])
        self.assertEqual(result["not_found"], ["9999"])

    async def test_no_op_when_nothing_matches_skips_save_and_reload(self):
        coord = self._make_coordinator_stub()
        result = await coord.purge_inventory_addresses(["9999", "8888"])

        self.assertEqual(result["removed_modules"], [])
        self.assertEqual(result["removed_buttons"], [])
        self.assertEqual(set(result["not_found"]), {"9999", "8888"})
        coord.module_storage.async_save.assert_not_awaited()
        coord.button_storage.async_save.assert_not_awaited()
        coord._rebuild_dict_module_data.assert_not_called()
        coord.hass.async_create_task.assert_not_called()

    async def test_address_in_both_stores_removed_from_both(self):
        coord = self._make_coordinator_stub()
        # Synthesize an address that exists as both a module key and a
        # button key — would be a hash collision in practice but the
        # purge logic shouldn't care; both stores get the pop.
        coord.module_storage.data["nikobus_module"]["1234"] = {"module_type": "switch_module"}
        coord.dict_button_data["nikobus_button"]["1234"] = {"type": "Button"}

        result = await coord.purge_inventory_addresses(["1234"])

        self.assertEqual(result["removed_modules"], ["1234"])
        self.assertEqual(result["removed_buttons"], ["1234"])
        self.assertEqual(result["not_found"], [])

    async def test_empty_input_is_no_op(self):
        coord = self._make_coordinator_stub()
        result = await coord.purge_inventory_addresses([])

        self.assertEqual(result, {"removed_modules": [], "removed_buttons": [], "not_found": []})
        coord.module_storage.async_save.assert_not_awaited()
        coord.hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Post-discovery stale-inventory reconciliation (issue #319)
# ---------------------------------------------------------------------------

class TestReconcilePostDiscovery(unittest.IsolatedAsyncioTestCase):
    """Verify the reconciliation that fires from _handle_discovery_finished.

    Three behaviours pinned: (1) absent modules get evicted from the store,
    (2) buttons whose linked_modules are entirely absent are tagged
    ``legacy_orphan``, (3) buttons with no linked_modules are tagged
    ``legacy_undecoded``.
    """

    async def asyncSetUp(self) -> None:
        # Defensive: patch ``asyncio.sleep`` in the coordinator module
        # so any reconciliation-path sleep can't wedge a test. With
        # nikobus-connect 0.5.20 owning the outer retry loop, the
        # coordinator no longer sleeps during reconciliation — but
        # keeping the patch costs nothing and protects future code.
        self._sleep_patcher = patch(
            "custom_components.nikobus.coordinator.asyncio.sleep",
            new=AsyncMock(),
        )
        self._sleep_patcher.start()
        self.addAsyncCleanup(self._stop_sleep_patcher)

    async def _stop_sleep_patcher(self) -> None:
        self._sleep_patcher.stop()

    def _make_coordinator_stub(
        self,
        *,
        modules: dict | None = None,
        buttons: dict | None = None,
        manifest: dict | None = None,
        has_method: bool = True,
        inventory_query_type=None,
        discovered_devices: dict | None = None,
        last_module_scan_was_full: bool = False,
    ):
        coord = MagicMock()
        coord.module_storage = MagicMock()
        coord.module_storage.data = {"nikobus_module": dict(modules or {})}
        coord.module_storage.async_save = AsyncMock()
        coord.button_storage = MagicMock()
        coord.button_storage.async_save = AsyncMock()
        coord.dict_button_data = {"nikobus_button": dict(buttons or {})}
        coord._rebuild_dict_module_data = MagicMock()
        coord.invalidate_controlled_by_index = MagicMock()
        coord.config_entry = MagicMock()
        coord.config_entry.entry_id = "entry_test"
        coord.hass = MagicMock()
        coord.hass.data = {}
        # Default to None so the eviction branch is inert in existing
        # tests; explicit PC_LINK + dict opt-in for the new tests below.
        coord.inventory_query_type = inventory_query_type
        # Stage-2 scan-all completion gate for the legacy-undecoded
        # Repairs issue. Default False — the issue only surfaces when
        # the caller explicitly opts in via ``last_module_scan_was_full``.
        coord._last_module_scan_was_full = last_module_scan_was_full
        # 2.12.0: ``_reconcile_post_discovery`` awaits
        # ``_ingest_cf_broadcasts`` to mirror the library's classified
        # CF activation broadcasts into ``cf_storage``. Stub here so
        # the existing reconcile tests keep covering their original
        # surface without needing to know about CFs.
        coord._ingest_cf_broadcasts = AsyncMock()
        coord.cf_storage = MagicMock()
        coord.cf_storage.data = {"nikobus_cf": {}}
        coord.cf_storage.async_save = AsyncMock()
        if has_method:
            coord.nikobus_discovery = MagicMock()
            default_manifest = manifest or {
                "checked": [],
                "present_modules": [],
                "absent_modules": [],
                "orphaned_buttons": [],
            }
            coord.nikobus_discovery.detect_stale_inventory = AsyncMock(
                return_value=default_manifest
            )
            coord.nikobus_discovery.discovered_devices = (
                dict(discovered_devices) if discovered_devices is not None else None
            )

        if not has_method:
            # Library lacks the method (older nikobus-connect): the spec
            # requires graceful skip-with-warning.
            class _OldDiscovery:
                pass
            coord.nikobus_discovery = _OldDiscovery()

        # The record_source / linked-module helpers are now pure functions
        # in nkbreconcile (imported into the coordinator module), so
        # ``_reconcile_post_discovery`` uses the real implementations
        # directly — no per-instance wiring needed.
        # Bind ``_reconcile_post_discovery`` to forward the sweep state
        # the way ``_handle_discovery_finished`` does in production:
        # via the kwargs that nikobus-connect 0.5.20 passes through
        # ``on_discovery_finished``. Tests can override by calling the
        # method with explicit args.
        bound_devices = (
            dict(discovered_devices) if discovered_devices is not None else None
        )
        bound_qtype = inventory_query_type
        coord._reconcile_post_discovery = (
            lambda self_=coord, devices=bound_devices, qtype=bound_qtype:
            NikobusDataCoordinator._reconcile_post_discovery(self_, devices, qtype)
        )
        return coord

    @staticmethod
    def _button(*module_addrs: str) -> dict:
        """Build a minimal physical-button record with one op-point linked
        to the given module addresses."""
        if module_addrs:
            op_points = {
                "1A": {
                    "bus_address": "AABBCC",
                    "linked_modules": [
                        {"module_address": addr, "outputs": [{"channel": 1}]}
                        for addr in module_addrs
                    ],
                }
            }
        else:
            op_points = {"1A": {"bus_address": "AABBCC", "linked_modules": []}}
        return {"type": "Button", "operation_points": op_points}

    async def test_absent_module_is_evicted_from_store(self):
        # Combined predicate: CCDD is absent from BOTH the sweep and the
        # probe → evicted. AABB is in the sweep → kept.
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"},
                     "CCDD": {"module_type": "dimmer_module"}},
            manifest={
                "checked": ["AABB", "CCDD"],
                "present_modules": ["AABB"],
                "absent_modules": ["CCDD"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])
        self.assertNotIn("CCDD", coord.module_storage.data["nikobus_module"])
        coord.module_storage.async_save.assert_awaited_once()
        coord.button_storage.async_save.assert_awaited_once()

    async def test_button_linked_to_absent_module_only_is_legacy_orphan(self):
        # CCDD is absent from BOTH sweep and probe → evicted. The button
        # only linked to CCDD has zero remaining links → legacy_orphan.
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
            buttons={"112233": self._button("CCDD")},
            manifest={
                "checked": ["AABB", "CCDD"],
                "present_modules": ["AABB"],
                "absent_modules": ["CCDD"],
                "orphaned_buttons": ["112233"],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["112233"]["status"],
            "legacy_orphan",
        )

    async def test_button_with_no_linked_modules_is_legacy_undecoded(self):
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
            buttons={"112233": self._button()},  # no linked_modules
            manifest={
                "checked": ["AABB"],
                "present_modules": ["AABB"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["112233"]["status"],
            "legacy_undecoded",
        )

    async def test_synthesized_pc_logic_input_is_not_flagged_as_legacy(self):
        """PC-Logic (05-201) and Modular Interface (05-206) synthesized
        inputs carry ``pc_logic_parent_address`` set by the library's
        ``_synthesize_pc_logic_inputs``. They model bus-event sources
        the parent module listens to internally, not buttons that drive
        output modules — empty ``linked_modules`` is their steady state.

        Pre-fix the classifier flagged all six as ``legacy_undecoded``
        and the Repairs flow asked the user to purge them (gist
        reproducer at install with parent 8DC8 + synthesised children
        64A061..64A066). The fix routes them to ``synthesized_input``
        so the legacy-undecoded Repairs issue ignores them.
        """
        synthesized = {
            "1A": {"bus_address": "21814B", "linked_modules": []},
            "1B": {"bus_address": "61814B", "linked_modules": []},
        }
        synthesized_input = {
            "type": "PC-Logic Logical Input",
            "model": "05-201",
            "operation_points": synthesized,
            "pc_logic_parent_address": "940C",
            "pc_logic_parent_type": "pc_logic",
        }
        modular_input = {
            "type": "Modular Interface Input",
            "model": "05-206",
            "operation_points": synthesized,
            "pc_logic_parent_address": "1234",
            "pc_logic_parent_type": "interface_module",
        }
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
            buttons={
                "64A061": synthesized_input,
                "0E1234": modular_input,
                # A wall button with no links — must still be flagged.
                "112233": self._button(),
            },
            manifest={
                "checked": ["AABB"],
                "present_modules": ["AABB"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
        )

        await coord._reconcile_post_discovery()

        buttons = coord.dict_button_data["nikobus_button"]
        self.assertEqual(buttons["64A061"]["status"], "synthesized_input")
        self.assertEqual(buttons["0E1234"]["status"], "synthesized_input")
        # Real wall button still flagged — the synthesized bypass must
        # be scoped to pc_logic_parent-bearing entries only.
        self.assertEqual(buttons["112233"]["status"], "legacy_undecoded")

    async def test_universal_interface_is_tagged_input_only(self):
        """Niko 05-058 Universal Interface (both switch and push-button
        modes) emits bus press telegrams from its 4/8 dry contacts but
        never writes into output-module link tables — its inputs feed
        PC-Logic conditions, like the synthesized PC-Logic Logical
        Inputs already handled above.

        Pre-fix the bucketing tagged it as ``legacy_undecoded`` and the
        Repairs flow false-positive'd asking the user to purge a
        perfectly-functional input device (1634DC in the test install,
        wired to garage-door reed switches). Fix routes input-only
        types to a dedicated ``input_only`` bucket the Repairs alert
        ignores.
        """
        no_links = {
            "1A": {"bus_address": "AECB1A"},
            "1B": {"bus_address": "EECB1A"},
            "1C": {"bus_address": "2ECB1A"},
            "1D": {"bus_address": "6ECB1A"},
            "2A": {"bus_address": "8ECB1A"},
            "2B": {"bus_address": "CECB1A"},
            "2C": {"bus_address": "0ECB1A"},
            "2D": {"bus_address": "4ECB1A"},
        }
        switch_mode = {
            "type": "Universal interface, switch mode",
            "model": "05-058",
            "channels": 8,
            "operation_points": no_links,
        }
        push_mode = {
            "type": "Universal interface, push-button mode",
            "model": "05-058",
            "channels": 4,
            "operation_points": {k: no_links[k] for k in ("1A", "1B", "1C", "1D")},
        }
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
            buttons={
                "1634DC": switch_mode,
                "0FA001": push_mode,
                # Control: a regular wall button with no links is still
                # tagged legacy_undecoded — the input_only exclusion
                # must be type-scoped, not blanket.
                "112233": self._button(),
            },
            manifest={
                "checked": ["AABB"],
                "present_modules": ["AABB"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
        )

        await coord._reconcile_post_discovery()

        buttons = coord.dict_button_data["nikobus_button"]
        self.assertEqual(buttons["1634DC"]["status"], "input_only")
        self.assertEqual(buttons["0FA001"]["status"], "input_only")
        # Real wall button still flagged.
        self.assertEqual(buttons["112233"]["status"], "legacy_undecoded")

    async def test_button_with_at_least_one_present_link_is_active(self):
        # Button linked to AABB (in sweep, kept) + CCDD (absent in both,
        # evicted). At least one link survives → active.
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"},
                     "CCDD": {"module_type": "switch_module"}},
            buttons={"112233": self._button("AABB", "CCDD")},
            manifest={
                "checked": ["AABB", "CCDD"],
                "present_modules": ["AABB"],
                "absent_modules": ["CCDD"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["112233"]["status"],
            "active",
        )

    async def test_module_address_comparison_is_case_insensitive(self):
        # Storage might hold lowercase addresses (legacy data); manifest
        # and discovered_devices are upper-case. Reconciliation must
        # still match — combined predicate keeps that contract.
        coord = self._make_coordinator_stub(
            modules={"aabb": {"module_type": "switch_module"}},
            manifest={
                "checked": ["AABB"],
                "present_modules": [],
                "absent_modules": ["AABB"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            # Sweep needs to be non-empty for eviction to fire; pin a
            # different address so the lower-case 'aabb' is the one
            # being tested for case-insensitive eviction.
            discovered_devices={"FFFF": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertNotIn("aabb", coord.module_storage.data["nikobus_module"])
        self.assertNotIn("AABB", coord.module_storage.data["nikobus_module"])

    async def test_skips_when_library_lacks_detect_method(self):
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
            buttons={"112233": self._button("AABB")},
            has_method=False,
        )

        await coord._reconcile_post_discovery()

        # Nothing saved, nothing tagged — graceful no-op.
        coord.module_storage.async_save.assert_not_awaited()
        coord.button_storage.async_save.assert_not_awaited()
        self.assertNotIn(
            "status", coord.dict_button_data["nikobus_button"]["112233"]
        )

    async def test_skips_when_no_discovery_attached(self):
        coord = self._make_coordinator_stub()
        coord.nikobus_discovery = None

        await coord._reconcile_post_discovery()

        coord.module_storage.async_save.assert_not_awaited()

    async def test_probe_failure_is_logged_and_swallowed(self):
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
        )
        coord.nikobus_discovery.detect_stale_inventory = AsyncMock(
            side_effect=RuntimeError("bus busy")
        )

        # Should not raise.
        await coord._reconcile_post_discovery()

        # Storage stays untouched on probe failure.
        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])
        coord.module_storage.async_save.assert_not_awaited()

    # ------------------------------------------------------------------
    # Combined sweep ∪ probe predicate (issue #319 regression set)
    # ------------------------------------------------------------------

    async def test_passes_outer_retry_kwargs_to_library(self):
        # nikobus-connect 0.5.20 owns the outer retry loop. We pass
        # ``outer_attempts=2, outer_delay=3.0`` (tuning from IKIKN
        # forensic) and trust the library to do retries + bus-quiet
        # delays + dedup-safe re-queue. No HA-side loop.
        coord = self._make_coordinator_stub(
            modules={"8110": {"module_type": "switch_module"}},
            manifest={
                "checked": ["8110"],
                "present_modules": ["8110"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"8110": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        coord.nikobus_discovery.detect_stale_inventory.assert_awaited_once_with(
            outer_attempts=2,
            outer_delay=3.0,
        )
        self.assertIn("8110", coord.module_storage.data["nikobus_module"])

    async def test_all_retries_failing_evicts_module_in_sweep(self):
        # 3D28-style residue surfaced by the 0.5.18 no-terminator
        # sweep: in discovered_devices but physically not on the bus.
        # Library-side outer retries (PR #55 in nikobus-connect 0.5.20)
        # confirm it's genuinely absent. Predicate: evict everything
        # in ``absent_modules``.
        coord = self._make_coordinator_stub(
            modules={"3D28": {"module_type": "switch_module"}},
            manifest={
                "checked": ["3D28"],
                "present_modules": [],
                "absent_modules": ["3D28"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"3D28": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertNotIn("3D28", coord.module_storage.data["nikobus_module"])

    async def test_healthy_install_no_evictions_single_probe_call(self):
        # Every probed module ACKs. Library's outer retry loop early-
        # exits internally; from our side we just see a clean manifest
        # with all addresses in ``present_modules``. No evictions.
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"},
                     "CCDD": {"module_type": "switch_module"}},
            manifest={
                "checked": ["AABB", "CCDD"],
                "present_modules": ["AABB", "CCDD"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"},
                                "CCDD": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.nikobus_discovery.detect_stale_inventory.await_count, 1
        )
        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])
        self.assertIn("CCDD", coord.module_storage.data["nikobus_module"])

    async def test_reconcile_uses_kwargs_for_sweep_state(self):
        # nikobus-connect 0.5.20 passes ``discovered_devices`` and
        # ``inventory_query_type`` through the callback (PR #55). The
        # reconciliation reads its sweep set from those kwargs — no
        # instance-state lifecycle dependency. Pin: passing the kwargs
        # produces eviction; passing ``None`` produces no eviction
        # even with stale instance state present.
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"},
                     "3D28": {"module_type": "switch_module"}},
            manifest={
                "checked": ["AABB", "3D28"],
                "present_modules": ["AABB"],
                "absent_modules": ["3D28"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"},
                                "3D28": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])
        self.assertNotIn("3D28", coord.module_storage.data["nikobus_module"])

    async def test_status_messages_track_probe_progress(self):
        # During the probe phase the diagnostic message used to freeze
        # on the last inventory frame ("PC Link inventory: 27/240
        # registers, 23 device(s) found") for the full 5-15 s window.
        # Verify _reconcile_post_discovery pushes descriptive messages
        # at the two transitions: probing → reconciling. (Retry-loop
        # messages are gone — the library owns retries now.)
        coord = self._make_coordinator_stub(
            modules={"8110": {"module_type": "switch_module"}},
            manifest={
                "checked": ["8110"],
                "present_modules": ["8110"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"8110": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        messages = [
            call.kwargs.get("message")
            for call in coord._update_discovery_state.call_args_list
            if call.kwargs.get("message") is not None
        ]
        self.assertTrue(
            any("Probing modules" in m for m in messages),
            f"missing probing message; got {messages}",
        )
        self.assertTrue(
            any("Reconciling" in m for m in messages),
            f"missing reconciling message; got {messages}",
        )

    async def test_status_message_reports_eviction_count(self):
        # When the predicate evicts at least one module, the user should
        # see the count surfaced in the diagnostic message rather than
        # finding it only in the log.
        coord = self._make_coordinator_stub(
            modules={"3D28": {"module_type": "switch_module"}},
            manifest={
                "checked": ["3D28"],
                "present_modules": [],
                "absent_modules": ["3D28"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"3D28": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        messages = [
            call.kwargs.get("message")
            for call in coord._update_discovery_state.call_args_list
            if call.kwargs.get("message") is not None
        ]
        self.assertTrue(
            any("Evicted 1 stale module" in m for m in messages),
            f"missing eviction count message; got {messages}",
        )

    async def test_module_only_in_probe_present_is_kept(self):
        # Edge case: module wired and ACKing the bus, but not in the
        # current PC-Link project (so absent from the sweep). Combined
        # predicate keeps it — user can purge_stale_inventory if they
        # want it gone.
        coord = self._make_coordinator_stub(
            modules={"7777": {"module_type": "switch_module"}},
            manifest={
                "checked": ["7777"],
                "present_modules": ["7777"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            # Sweep contains a different module so eviction-step is
            # eligible to fire; 7777 is missing from it.
            discovered_devices={"AAAA": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertIn("7777", coord.module_storage.data["nikobus_module"])

    async def test_module_in_neither_sweep_nor_probe_present_is_evicted(self):
        # IKIKN's 3D28: the previous owner's residue. Not in current
        # sweep, doesn't ACK probe → evicted. The intended behaviour
        # of post-discovery reconciliation.
        coord = self._make_coordinator_stub(
            modules={"3D28": {"module_type": "switch_module"},
                     "AABB": {"module_type": "switch_module"}},
            manifest={
                "checked": ["3D28", "AABB"],
                "present_modules": ["AABB"],
                "absent_modules": ["3D28"],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertNotIn("3D28", coord.module_storage.data["nikobus_module"])
        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])

    async def test_module_scan_does_not_evict_modules_not_in_discovered_devices(self):
        # Same Store + same single-module sweep output, but the user ran
        # a per-module scan (InventoryQueryType.MODULE), so step 2 must
        # not fire — otherwise re-scanning one module would wipe the rest.
        coord = self._make_coordinator_stub(
            modules={
                "AABB": {"module_type": "switch_module"},
                "CCDD": {"module_type": "switch_module"},
            },
            manifest={
                "checked": ["AABB", "CCDD"],
                "present_modules": ["AABB", "CCDD"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            discovered_devices={"CCDD": {"category": "Module"}},
        )

        await coord._reconcile_post_discovery()

        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])
        self.assertIn("CCDD", coord.module_storage.data["nikobus_module"])

    async def test_empty_discovered_devices_does_not_wipe_store(self):
        # A failed read produces an empty discovered_devices dict; we
        # must not interpret that as "the project is empty" and evict
        # everything. Step 1 still runs normally.
        coord = self._make_coordinator_stub(
            modules={
                "AABB": {"module_type": "switch_module"},
                "CCDD": {"module_type": "switch_module"},
            },
            manifest={
                "checked": ["AABB", "CCDD"],
                "present_modules": ["AABB", "CCDD"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={},
        )

        await coord._reconcile_post_discovery()

        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])
        self.assertIn("CCDD", coord.module_storage.data["nikobus_module"])

    async def test_pc_link_sweep_ignores_non_module_categories(self):
        # discovered_devices also contains buttons under category="Button".
        # Only entries with category=="Module" should count toward the
        # currently-swept set; otherwise a real module that just happens
        # not to share an address with any button would get wrongly evicted.
        coord = self._make_coordinator_stub(
            modules={"AABB": {"module_type": "switch_module"}},
            manifest={
                "checked": ["AABB"],
                "present_modules": ["AABB"],
                "absent_modules": [],
                "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={
                "AABB": {"category": "Module"},
                "FFEE": {"category": "Button"},
            },
        )

        await coord._reconcile_post_discovery()

        self.assertIn("AABB", coord.module_storage.data["nikobus_module"])

    # ------------------------------------------------------------------
    # Stage-2 scan-all → legacy_undecoded buttons Repairs issue
    # ------------------------------------------------------------------

    async def test_legacy_undecoded_repairs_surfaces_after_scan_all(self):
        # After a Stage-2 scan-all, ``legacy_undecoded`` is a meaningful
        # signal: every output module's register table was just read,
        # so a button with no decoded ``linked_modules`` is either
        # intentionally unwired (HA automation trigger) or residue —
        # HA can't tell them apart, so it surfaces a Repairs issue and
        # lets the user choose. We assert the helper is called with the
        # button dict so the Repairs issue gets created downstream.
        coord = self._make_coordinator_stub(
            modules={},
            buttons={"1843B4": {
                "type": "Bus push button, 4 control buttons",
                "model": "05-346",
            }},
            manifest={
                "checked": [], "present_modules": [],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            last_module_scan_was_full=True,
        )

        await coord._reconcile_post_discovery()

        coord._surface_legacy_undecoded_buttons.assert_called_once()

    async def test_legacy_undecoded_repairs_NOT_surfaced_after_pc_link(self):
        # After Stage-1 (PC-Link inventory), almost every button reads
        # as ``legacy_undecoded`` by default — no module register table
        # has been decoded yet. Surfacing the issue here would propose
        # purging every button on a fresh install. Gate it on the
        # scan-all flag.
        coord = self._make_coordinator_stub(
            modules={},
            buttons={"1843B4": {
                "type": "Bus push button, 4 control buttons",
            }},
            manifest={
                "checked": [], "present_modules": [],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.PC_LINK,
            discovered_devices={"AABB": {"category": "Module"}},
            last_module_scan_was_full=False,
        )

        await coord._reconcile_post_discovery()

        coord._surface_legacy_undecoded_buttons.assert_not_called()

    async def test_legacy_undecoded_repairs_NOT_surfaced_after_single_module_scan(self):
        # Single-module register scan only updates ``linked_modules``
        # for buttons referenced by that one module. Other buttons may
        # still read as ``legacy_undecoded`` simply because we haven't
        # scanned their controlling module — not because they're
        # residue. The verdict is only trustworthy after scan-all.
        coord = self._make_coordinator_stub(
            modules={},
            buttons={"1843B4": {
                "type": "Bus push button, 4 control buttons",
            }},
            manifest={
                "checked": [], "present_modules": [],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            last_module_scan_was_full=False,
        )

        await coord._reconcile_post_discovery()

        coord._surface_legacy_undecoded_buttons.assert_not_called()

    # ------------------------------------------------------------------
    # nikobus-connect 0.5.22 record_source classification
    # ------------------------------------------------------------------

    @staticmethod
    def _button_with_outputs(*records: dict) -> dict:
        """Construct a button whose 1A op-point has ``linked_modules``
        carrying ``records`` as its outputs."""
        return {
            "type": "Bus push button",
            "operation_points": {
                "1A": {
                    "bus_address": "AABBCC",
                    "linked_modules": [
                        {"module_address": "8110", "outputs": list(records)},
                    ],
                }
            },
        }

    async def test_registry_only_outputs_classified_as_legacy_orphan_when_no_pc_logic(self):
        # IKIKN scenario: every output record for this button is
        # sourced from PC-Link / PC-Logic registry, and the install
        # has NO PC-Logic module. Unambiguous residue programming
        # from a previous owner — bucket as legacy_orphan.
        coord = self._make_coordinator_stub(
            modules={"8110": {"module_type": "switch_module"}},
            buttons={"3C4CE8": self._button_with_outputs(
                {"channel": 11, "mode": "M07",
                 "payload": "0A...", "button_address": "3C4CE8",
                 "record_source": "pc_link_registry"},
            )},
            manifest={
                "checked": ["8110"],
                "present_modules": ["8110"],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            last_module_scan_was_full=True,
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["3C4CE8"]["status"],
            "legacy_orphan",
        )

    async def test_registry_only_outputs_stay_active_when_pc_logic_present(self):
        # Same button shape, but the install has a PC-Logic module.
        # The registry-only output could be a legitimate PC-Logic
        # scene trigger; defer to the existing classifier. With
        # linked target (8110) surviving the probe, the button is
        # ``active`` and the user can adjudicate manually if needed.
        coord = self._make_coordinator_stub(
            modules={
                "8110": {"module_type": "switch_module"},
                "940C": {"module_type": "pc_logic"},
            },
            buttons={"3C4CE8": self._button_with_outputs(
                {"channel": 11, "mode": "M07",
                 "payload": "0A...", "button_address": "3C4CE8",
                 "record_source": "pc_link_registry"},
            )},
            manifest={
                "checked": ["8110"],
                "present_modules": ["8110"],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            last_module_scan_was_full=True,
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["3C4CE8"]["status"],
            "active",
        )

    async def test_mixed_record_sources_classified_active(self):
        # One output is sourced from the output module's own table
        # (authoritative current programming), another from PC-Link
        # registry (potentially residue). At least one real link
        # exists → button is active regardless of PC-Logic presence.
        coord = self._make_coordinator_stub(
            modules={"8110": {"module_type": "switch_module"}},
            buttons={"AABBCC": self._button_with_outputs(
                {"channel": 5, "mode": "M05",
                 "payload": "FF34F4...", "button_address": "AABBCC",
                 "record_source": "output_module_table"},
                {"channel": 11, "mode": "M07",
                 "payload": "0A...", "button_address": "AABBCC",
                 "record_source": "pc_link_registry"},
            )},
            manifest={
                "checked": ["8110"],
                "present_modules": ["8110"],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            last_module_scan_was_full=True,
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["AABBCC"]["status"],
            "active",
        )

    async def test_record_source_absent_falls_through_to_existing_classifier(self):
        # Pre-0.5.22 data: outputs have no ``record_source`` field.
        # Treat as source-unknown and let the existing
        # linked-vs-remaining check decide. With linked module 8110
        # surviving, this button is ``active`` — same behavior as
        # before 0.5.22 adoption.
        coord = self._make_coordinator_stub(
            modules={"8110": {"module_type": "switch_module"}},
            buttons={"AABBCC": self._button_with_outputs(
                {"channel": 5, "mode": "M05",
                 "payload": "FF34F4...", "button_address": "AABBCC"},
                # No record_source key — pre-0.5.22 record shape.
            )},
            manifest={
                "checked": ["8110"],
                "present_modules": ["8110"],
                "absent_modules": [], "orphaned_buttons": [],
            },
            inventory_query_type=InventoryQueryType.MODULE,
            last_module_scan_was_full=True,
        )

        await coord._reconcile_post_discovery()

        self.assertEqual(
            coord.dict_button_data["nikobus_button"]["AABBCC"]["status"],
            "active",
        )

    async def test_has_pc_logic_module_helper(self):
        # Direct unit test of the topology gate (now a pure helper).
        from custom_components.nikobus.nkbreconcile import has_pc_logic_module

        coord_no_pc_logic = self._make_coordinator_stub(
            modules={"8110": {"module_type": "switch_module"}},
        )
        coord_with_pc_logic = self._make_coordinator_stub(
            modules={
                "8110": {"module_type": "switch_module"},
                "940C": {"module_type": "pc_logic"},
            },
        )

        self.assertFalse(has_pc_logic_module(coord_no_pc_logic.module_storage.data))
        self.assertTrue(has_pc_logic_module(coord_with_pc_logic.module_storage.data))


class TestHandleDiscoveryFinishedFinalizing(unittest.IsolatedAsyncioTestCase):
    """2.11.3: ``_handle_discovery_finished`` must transition the sub-phase
    to FINALIZING *before* ``_reconcile_post_discovery`` runs, so the
    progress bar moves to the 95% floor instead of staying frozen at
    whatever the previous phase showed (30% for PC-Link inventory-only,
    95% for Scan-All) during the multi-second reconciliation work.

    Before this fix, a PC-Link inventory-only scan looked broken end-to-end:
    bar would climb to 30% during identity, then sit at 30% for the entire
    reconcile/probe/save window before jumping straight to 100% at the
    very end. Now the bar jumps to 95% the moment reconciliation starts,
    so the user sees clear visual progress.
    """

    async def test_finalizing_set_before_reconcile_runs(self):
        from custom_components.nikobus.const import (
            DISCOVERY_SUB_PHASE_FINALIZING,
            DISCOVERY_SUB_PHASE_IDENTITY,
        )
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        captured: list[str] = []

        coord = MagicMock()
        coord.discovery_register_current = None
        coord.discovery_sub_phase = DISCOVERY_SUB_PHASE_IDENTITY
        coord.discovery_decoded_records = 0
        coord.discovery_running = True
        coord._discovery_finished_event = MagicMock()
        coord._discovery_finished_event.set = MagicMock()
        coord._discovery_auto_reload = False
        coord.async_request_refresh = AsyncMock()

        async def fake_reconcile(*args, **kwargs):
            # Sample what sub_phase looks like the moment reconcile
            # starts — must be FINALIZING by this point, not IDENTITY.
            captured.append(coord.discovery_sub_phase)

        coord._reconcile_post_discovery = fake_reconcile

        # Bind real _handle_discovery_finished to the mock
        coord._handle_discovery_finished = (
            lambda *args, self_=coord, **kw:
            NikobusDataCoordinator._handle_discovery_finished(self_, *args, **kw)
        )

        await coord._handle_discovery_finished()

        self.assertEqual(
            captured,
            [DISCOVERY_SUB_PHASE_FINALIZING],
            "Sub-phase must be FINALIZING before _reconcile_post_discovery runs "
            f"so the bar advances to the 95% floor; was {captured!r}",
        )


class TestUnifiedStep1Discovery(unittest.IsolatedAsyncioTestCase):
    """2.11.5: ``start_pc_link_inventory`` is the unified step-1 entry
    point. It probes PC-Link first; on timeout it falls back to the
    manual config files; regardless of source, it overlays friendly
    names from the files onto the live stores afterwards.

    These tests pin the three branches:

      * PC-Link present → uses PC-Link inventory, overlay runs
      * PC-Link absent + files present → uses files as inventory
      * PC-Link absent + no files → raises ``no_inventory_source``
    """

    def _make_coord(self):
        from custom_components.nikobus.coordinator import NikobusDataCoordinator

        coord = MagicMock(spec=NikobusDataCoordinator)
        coord.discovery_running = False
        coord.nikobus_discovery = MagicMock()
        coord.nikobus_discovery.start_inventory_discovery = AsyncMock()
        coord.module_storage = MagicMock()
        coord.module_storage.async_save = AsyncMock()
        coord.button_storage = MagicMock()
        coord.button_storage.async_save = AsyncMock()
        coord.dict_button_data = {"nikobus_button": {}}
        coord.hass = MagicMock()
        coord._discovery_finished_event = asyncio.Event()
        coord._pclink_first_response_event = asyncio.Event()
        coord._discovery_auto_reload = False
        coord._last_module_scan_was_full = False
        coord._update_discovery_state = MagicMock()
        coord._rebuild_dict_module_data = MagicMock()
        coord._PCLINK_PROBE_TIMEOUT = 0.05  # speed up first-response wait
        coord._PCLINK_FINALIZE_WAIT_AFTER_TIMEOUT = 0.05  # and finalize wait
        coord._try_pclink_inventory = (
            lambda self_=coord:
            NikobusDataCoordinator._try_pclink_inventory(self_)
        )
        coord._apply_manual_inventory_as_fallback = (
            lambda self_=coord:
            NikobusDataCoordinator._apply_manual_inventory_as_fallback(self_)
        )
        coord.start_pc_link_inventory = (
            lambda self_=coord, **kw:
            NikobusDataCoordinator.start_pc_link_inventory(self_, **kw)
        )
        return coord

    async def test_pclink_present_completes_without_fallback(self):
        coord = self._make_coord()

        async def fake_start():
            # Library "succeeds" — simulate first PC-Link response then
            # full-completion event (mirrors the real callback sequence).
            coord._pclink_first_response_event.set()
            coord._discovery_finished_event.set()

        coord.nikobus_discovery.start_inventory_discovery.side_effect = (
            fake_start
        )

        await coord.start_pc_link_inventory(auto_reload=False)

        coord.nikobus_discovery.start_inventory_discovery.assert_awaited_once()

    async def test_pclink_timeout_falls_back_to_manual_files(self):
        coord = self._make_coord()

        async def hang():
            # Library never signals completion — _discovery_finished_event
            # never fires → wait_for() raises TimeoutError.
            await asyncio.sleep(10)

        coord.nikobus_discovery.start_inventory_discovery.side_effect = hang

        with patch(
            "custom_components.nikobus.nkbmanual.async_apply_manual_config",
            new_callable=AsyncMock, return_value=True,
        ) as apply_mock:
            await coord.start_pc_link_inventory(auto_reload=False)

        # Fallback fired (manual import called).
        apply_mock.assert_awaited_once()

    async def test_pclink_timeout_and_no_files_raises(self):
        from homeassistant.exceptions import HomeAssistantError

        coord = self._make_coord()

        async def hang():
            await asyncio.sleep(10)

        coord.nikobus_discovery.start_inventory_discovery.side_effect = hang

        with patch(
            "custom_components.nikobus.nkbmanual.async_apply_manual_config",
            new_callable=AsyncMock, return_value=False,
        ):
            with self.assertRaises(HomeAssistantError):
                await coord.start_pc_link_inventory(auto_reload=False)


class TestSurfaceLegacyUndecodedButtons(unittest.IsolatedAsyncioTestCase):
    """Direct tests for ``_surface_legacy_undecoded_buttons``.

    Asserts the create/delete contract on the HA issue registry so the
    Repairs UI shows up exactly when (and with what data) we expect.
    """

    def _coord_stub(self) -> MagicMock:
        coord = MagicMock()
        coord.hass = MagicMock()
        coord.config_entry = MagicMock()
        coord.config_entry.entry_id = "entry_test"
        return coord

    @patch("custom_components.nikobus.coordinator.ir.async_create_issue")
    @patch("custom_components.nikobus.coordinator.ir.async_delete_issue")
    def test_creates_issue_for_both_legacy_buckets(
        self, mock_delete, mock_create
    ):
        # Both ``legacy_undecoded`` and ``legacy_orphan`` are surfaced
        # together for user review: undecoded = no links anywhere,
        # orphan = residue programming or fully-evicted targets. Both
        # warrant a "purge or keep?" decision.
        coord = self._coord_stub()
        buttons = {
            "1843B4": {"status": "legacy_undecoded",
                       "type": "Bus push button"},
            "0C2387": {"status": "legacy_undecoded",
                       "type": "RF transmitter"},
            "AABBCC": {"status": "legacy_orphan",
                       "type": "Bus push button"},
            "10152B": {"status": "active"},  # must be filtered out
        }

        NikobusDataCoordinator._surface_legacy_undecoded_buttons(coord, buttons)

        mock_delete.assert_not_called()
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["is_fixable"], True)
        self.assertEqual(
            call_kwargs["translation_key"], "legacy_undecoded_buttons"
        )
        self.assertEqual(
            call_kwargs["translation_placeholders"], {"count": "3"}
        )
        # Addresses sorted, uppercase. Both buckets present, ``active``
        # filtered out.
        self.assertEqual(
            call_kwargs["data"]["addresses"],
            ["0C2387", "1843B4", "AABBCC"],
        )
        self.assertEqual(
            call_kwargs["data"]["entry_id"], "entry_test"
        )

    @patch("custom_components.nikobus.coordinator.ir.async_create_issue")
    @patch("custom_components.nikobus.coordinator.ir.async_delete_issue")
    def test_deletes_issue_when_no_legacy_buttons_remain(
        self, mock_delete, mock_create
    ):
        # All buttons in ``active`` — neither legacy bucket has any
        # entry. The Repairs issue should be cleared if previously open.
        coord = self._coord_stub()
        buttons = {
            "1843B4": {"status": "active"},
            "0C2387": {"status": "active"},
        }

        NikobusDataCoordinator._surface_legacy_undecoded_buttons(coord, buttons)

        mock_create.assert_not_called()
        mock_delete.assert_called_once()
        # Same issue_id is used for both create and delete so they
        # target the same Repairs entry.
        delete_args = mock_delete.call_args
        self.assertIn("legacy_undecoded_buttons", delete_args.args[2])
        self.assertIn("entry_test", delete_args.args[2])

    @patch("custom_components.nikobus.coordinator.ir.async_create_issue")
    @patch("custom_components.nikobus.coordinator.ir.async_delete_issue")
    def test_synthesized_inputs_do_not_surface_repair(
        self, mock_delete, mock_create
    ):
        # Repro for the false-positive on synthesized PC-Logic /
        # Modular Interface inputs: every 05-201 child carries empty
        # ``linked_modules`` by design, which pre-fix bucketed as
        # ``legacy_undecoded`` and walked into this Repairs flow asking
        # the user to purge their PC-Logic inputs. With the fix in
        # ``_reconcile_post_discovery``, they land on
        # ``synthesized_input`` and this surface filters them out.
        coord = self._coord_stub()
        buttons = {
            "64A061": {"status": "synthesized_input"},
            "64A062": {"status": "synthesized_input"},
            "1843B4": {"status": "active"},
        }

        NikobusDataCoordinator._surface_legacy_undecoded_buttons(coord, buttons)

        # No legacy entries at all — no repair issue should be created.
        mock_create.assert_not_called()
        mock_delete.assert_called_once()


# ---------------------------------------------------------------------------
# Total-blackout auto-recovery (issue #337)
# ---------------------------------------------------------------------------

class TestBlackoutAutoRecovery(unittest.IsolatedAsyncioTestCase):
    """Pin the polling-cycle blackout detection that triggers reconnect.

    On idle-induced PC-Link / FTDI sleep, every poll in a single cycle
    times out without the serial FD raising any error — the coordinator's
    standard reconnect supervisor (which watches FD errors) doesn't
    notice. This tests the new aggregate-failure path that detects
    "all polls failed" and kicks ``_handle_connection_lost`` so the
    integration self-heals.
    """

    def _make_coord(
        self,
        modules: dict[str, dict],
        *,
        get_output_state_side_effect=None,
    ):
        coord = MagicMock()
        coord.discovery_running = False
        coord._stopping = False
        coord.dict_module_data = modules
        coord._module_states = {}
        coord.nikobus_command = MagicMock()
        coord.nikobus_command.get_output_state = AsyncMock(
            side_effect=get_output_state_side_effect
        )
        coord.async_event_handler = AsyncMock()
        coord.hass = MagicMock()
        coord._handle_connection_lost = AsyncMock()

        # Capture the background-task callable so we can assert on it.
        # Close the coroutine arg so Python doesn't warn about an
        # unawaited ``_handle_connection_lost()`` call slot.
        def _consume(coro=None, *, name=None):
            if coro is not None and hasattr(coro, "close"):
                coro.close()

        coord.hass.async_create_background_task = MagicMock(
            side_effect=_consume
        )
        # Bind real _refresh_module_type so the count-tracking logic
        # actually runs against the mocked get_output_state. The
        # unbound staticmethod-style call hands ``coord`` in as self.
        coord._refresh_module_type = (
            lambda md, _coord=coord:
            NikobusDataCoordinator._refresh_module_type(_coord, md)
        )
        return coord

    async def test_total_blackout_triggers_reconnect(self):
        # Every poll fails → reconnect kicked.
        from nikobus_connect.exceptions import NikobusTimeoutError
        coord = self._make_coord(
            modules={"switch_module": {
                "8110": {"channels": [{}, {}, {}, {}, {}, {}, {}]},  # 7 channels → 2 groups
                "1CEC": {"channels": [{}, {}, {}, {}]},               # 4 channels → 1 group
            }},
            get_output_state_side_effect=NikobusTimeoutError("timeout"),
        )

        await NikobusDataCoordinator._async_update_data(coord)

        # 8110 has 7 channels → groups (1, 2) = 2 polls
        # 1CEC has 4 channels → groups (1,)  = 1 poll
        # Total = 3 polls, all failed → reconnect triggered.
        coord.hass.async_create_background_task.assert_called_once()
        # Verify the task is for connection recovery (background task
        # name matches our new label).
        call_kwargs = coord.hass.async_create_background_task.call_args.kwargs
        self.assertEqual(call_kwargs.get("name"), "nikobus_blackout_recovery")

    async def test_partial_failure_no_reconnect(self):
        # 2 of 3 polls fail; 1 succeeds → blackout NOT triggered.
        # ``side_effect=list`` consumes one entry per call.
        from nikobus_connect.exceptions import NikobusTimeoutError
        coord = self._make_coord(
            modules={"switch_module": {
                "8110": {"channels": [{}, {}, {}, {}, {}, {}, {}]},
                "1CEC": {"channels": [{}, {}, {}, {}]},
            }},
            get_output_state_side_effect=[
                "0000FFFF0000FFFE2245",        # success — 12-hex-char state
                NikobusTimeoutError("timeout"),
                NikobusTimeoutError("timeout"),
            ],
        )

        await NikobusDataCoordinator._async_update_data(coord)

        # Some polls succeeded — not a blackout. No reconnect.
        coord.hass.async_create_background_task.assert_not_called()

    async def test_all_polls_succeed_no_reconnect(self):
        coord = self._make_coord(
            modules={"switch_module": {
                "8110": {"channels": [{}, {}, {}, {}]},
            }},
            get_output_state_side_effect=lambda *args, **kw: "0000FFFF000000",
        )

        await NikobusDataCoordinator._async_update_data(coord)

        coord.hass.async_create_background_task.assert_not_called()

    async def test_no_modules_no_reconnect(self):
        # Empty install (e.g., user removed all output modules) →
        # polled=0, so blackout check skips even though failures=0.
        coord = self._make_coord(modules={"switch_module": {}})

        await NikobusDataCoordinator._async_update_data(coord)

        coord.hass.async_create_background_task.assert_not_called()

    async def test_discovery_running_skips_polling(self):
        coord = self._make_coord(
            modules={"switch_module": {"8110": {"channels": [{}, {}]}}},
        )
        coord.discovery_running = True

        await NikobusDataCoordinator._async_update_data(coord)

        # Polling guard skipped the cycle entirely; no reconnect attempt.
        coord.hass.async_create_background_task.assert_not_called()
        coord.nikobus_command.get_output_state.assert_not_called()

    async def test_stopping_does_not_trigger_reconnect(self):
        # If the integration is shutting down, blackout-recovery
        # should NOT kick — we're going away anyway.
        from nikobus_connect.exceptions import NikobusTimeoutError
        coord = self._make_coord(
            modules={"switch_module": {"8110": {"channels": [{}, {}]}}},
            get_output_state_side_effect=NikobusTimeoutError("timeout"),
        )
        coord._stopping = True

        await NikobusDataCoordinator._async_update_data(coord)

        coord.hass.async_create_background_task.assert_not_called()


# ---------------------------------------------------------------------------
# Concurrent reconnect coalescing (issue #337 race fix)
# ---------------------------------------------------------------------------

class TestHandleConnectionLostCoalescing(unittest.IsolatedAsyncioTestCase):
    """Pin the early-exit dedup so concurrent ``_handle_connection_lost``
    calls don't race on ``nikobus_command.stop()`` mid-reconnect.

    The race surfaces when blackout-detection triggers reconnect (call
    #1) → reconnect's fresh ``connect()`` opens new FD → old listener's
    pending ``read()`` fails → listener fires ``on_connection_lost`` →
    ``_handle_connection_lost`` (call #2) → second ``command.stop()``
    runs concurrently with the in-flight handshake, producing the
    visible ``Reconnect 1 failed: Cannot send: Not connected.`` error.
    """

    def _make_coord(self, reconnect_task=None):
        coord = MagicMock()
        coord._stopping = False
        coord._reconnect_task = reconnect_task
        coord.nikobus_command = MagicMock()
        coord.nikobus_command.stop = AsyncMock()
        coord.nikobus_listener = MagicMock()
        coord.nikobus_listener.stop = AsyncMock()
        coord.async_update_listeners = MagicMock()
        coord.hass = MagicMock()
        # Capture the background-task creation; close coroutines so
        # we don't get unawaited-coroutine warnings from the mock.
        def _consume(coro=None, *, name=None):
            if coro is not None and hasattr(coro, "close"):
                coro.close()
            task = MagicMock()
            task.done = MagicMock(return_value=False)
            return task
        coord.hass.async_create_background_task = MagicMock(side_effect=_consume)
        return coord

    async def test_first_call_creates_reconnect_task(self):
        coord = self._make_coord()

        await NikobusDataCoordinator._handle_connection_lost(coord)

        coord.nikobus_command.stop.assert_awaited_once()
        # Listener MUST be stopped before the reconnect runs — its
        # pending read() on the old reader would otherwise fail when
        # connect() opens a new FD, and the library's read() handler
        # calls disconnect() which sets _is_connected=False
        # mid-handshake.
        coord.nikobus_listener.stop.assert_awaited_once()
        coord.hass.async_create_background_task.assert_called_once()

    async def test_concurrent_call_coalesces_to_noop(self):
        # _reconnect_task already exists and is in-flight (not done).
        in_flight = MagicMock()
        in_flight.done = MagicMock(return_value=False)
        coord = self._make_coord(reconnect_task=in_flight)

        await NikobusDataCoordinator._handle_connection_lost(coord)

        # Critical: command.stop() AND listener.stop() must NOT run a
        # second time — both would race with the in-flight handshake.
        coord.nikobus_command.stop.assert_not_awaited()
        coord.nikobus_listener.stop.assert_not_awaited()
        # And no new reconnect task is created — coalesced.
        coord.hass.async_create_background_task.assert_not_called()

    async def test_done_reconnect_task_allows_new_reconnect(self):
        # Previous reconnect completed → a NEW connection-lost event
        # should start a fresh reconnect.
        completed = MagicMock()
        completed.done = MagicMock(return_value=True)
        coord = self._make_coord(reconnect_task=completed)

        await NikobusDataCoordinator._handle_connection_lost(coord)

        coord.nikobus_command.stop.assert_awaited_once()
        coord.hass.async_create_background_task.assert_called_once()

    async def test_stopping_short_circuits(self):
        coord = self._make_coord()
        coord._stopping = True

        await NikobusDataCoordinator._handle_connection_lost(coord)

        coord.nikobus_command.stop.assert_not_awaited()
        coord.hass.async_create_background_task.assert_not_called()


# ---------------------------------------------------------------------------
# Helper: _collect_button_linked_modules
# ---------------------------------------------------------------------------

class TestCollectButtonLinkedModules(unittest.TestCase):
    """Pure-function unit tests for the helper that drives bucket assignment."""

    from custom_components.nikobus.nkbreconcile import collect_button_linked_modules

    _collect = staticmethod(collect_button_linked_modules)

    def test_no_op_points_returns_empty_set(self):
        self.assertEqual(self._collect({}), set())
        self.assertEqual(self._collect({"operation_points": {}}), set())

    def test_collects_addresses_across_all_op_points(self):
        phys = {
            "operation_points": {
                "1A": {"linked_modules": [
                    {"module_address": "aabb", "outputs": [{"channel": 1}]},
                ]},
                "1B": {"linked_modules": [
                    {"module_address": "CCDD", "outputs": [{"channel": 2}]},
                ]},
            }
        }
        self.assertEqual(self._collect(phys), {"AABB", "CCDD"})

    def test_skips_links_without_module_address(self):
        phys = {
            "operation_points": {
                "1A": {"linked_modules": [
                    {"module_address": "AABB", "outputs": []},
                    {"outputs": [{"channel": 1}]},  # malformed
                    "garbage",  # malformed
                ]}
            }
        }
        self.assertEqual(self._collect(phys), {"AABB"})


class TestRefreshModuleTypeDispatch(unittest.TestCase):
    """The poll wakes a module's entities only when its bytes changed; the
    coordinator's own post-poll global refresh covers the rest."""

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _setup(self, state_hex):
        c = _coord(states={})
        c.async_event_handler = AsyncMock()
        c.nikobus_command.get_output_state = AsyncMock(return_value=state_hex)
        return c

    def test_dispatches_on_first_read_and_on_change_only(self):
        c = self._setup("0102030405060708090A0B0C")
        modules = {"C1C7": {"channels": [1, 2, 3, 4]}}

        polled, failed = self._run(c._refresh_module_type(modules))
        self.assertEqual((polled, failed), (1, 0))
        self.assertEqual(c.async_event_handler.await_count, 1)  # empty -> real

        self._run(c._refresh_module_type(modules))              # identical poll
        self.assertEqual(c.async_event_handler.await_count, 1)  # no extra wake

        c.nikobus_command.get_output_state = AsyncMock(
            return_value="FF02030405060708090A0B0C"
        )
        self._run(c._refresh_module_type(modules))              # byte changed
        self.assertEqual(c.async_event_handler.await_count, 2)

    def test_unchanged_module_never_dispatches(self):
        c = _coord(states={"C1C7": bytearray.fromhex("0102030405060708090A0B0C")})
        c.async_event_handler = AsyncMock()
        c.nikobus_command.get_output_state = AsyncMock(
            return_value="0102030405060708090A0B0C"
        )
        self._run(c._refresh_module_type({"C1C7": {"channels": [1, 2, 3, 4]}}))
        c.async_event_handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
