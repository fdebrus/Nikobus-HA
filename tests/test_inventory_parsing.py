import importlib.util
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from nikobus_connect.discovery import DecodedCommand, InventoryResult, NikobusDiscovery
from nikobus_connect.discovery.protocol import DecoderContext, normalize_payload, reverse_hex
from nikobus_connect.discovery.shutter_decoder import decode as shutter_decode


def _build_inventory_payload(device_type: int, address_bytes: bytes, length: int = 18) -> str:
    payload = bytearray([0] * length)
    payload[7] = device_type
    payload[11 : 11 + len(address_bytes)] = address_bytes
    return payload.hex().upper()


class TestInventoryParsing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        class FakeCoordinator:
            def __init__(self) -> None:
                self.discovery_module = False
                self.discovery_module_address = None
                self.discovery_running = False
                self.dict_module_data = {}
                self.inventory_query_type = None

            def get_module_type(self, address):
                return "switch_module"

            def get_module_channel_count(self, address):
                return 12

            def apply_inventory_update(self, inventory_result, discovered_devices):
                for device in inventory_result.modules + inventory_result.buttons:
                    discovered_devices[device["address"]] = device

        self.coordinator = FakeCoordinator()
        self._tmp_dir = tempfile.mkdtemp()
        self.discovery = NikobusDiscovery(
            self.coordinator,
            config_dir=self._tmp_dir,
            create_task=MagicMock(return_value=MagicMock()),
        )

    async def test_parse_pclink_inventory_response_populates_modules_and_buttons(self):
        module_payload = _build_inventory_payload(0x01, b"\x12\x34")
        button_payload = _build_inventory_payload(0x04, b"\xAB\xCD\xEF")

        module_result = await self.discovery.parse_inventory_response(module_payload)
        button_result = await self.discovery.parse_inventory_response(button_payload)

        self.assertIsInstance(module_result, InventoryResult)
        self.assertIsInstance(button_result, InventoryResult)
        self.assertEqual(len(module_result.modules), 1)
        self.assertEqual(len(button_result.buttons), 1)
        self.assertIn("3412", self.discovery.discovered_devices)
        self.assertIn("EFCDAB", self.discovery.discovered_devices)

    async def test_parse_module_inventory_response_populates_output_button_mapping(self):
        class FakeDecoder:
            module_type = "switch_module"

            def can_handle(self, module_type: str) -> bool:
                return module_type == self.module_type

            def set_module_address(self, address: str) -> None:
                return None

            def set_module_channel_count(self, count: int | None) -> None:
                return None

            def analyze_frame_payload(self, payload_buffer: str, payload_and_crc: str):
                return {"chunks": [payload_and_crc], "remainder": ""}

            def decode(self, message: str, module_address: str | None = None):
                return [
                    DecodedCommand(
                        module_type="switch_module",
                        raw_message=message,
                        metadata={
                            "push_button_address": "AA0000",
                            "key_raw": 1,
                            "channel": 1,
                            "M": "M01",
                            "T1": None,
                            "T2": None,
                            "payload": "FFFF",
                            "button_address": "BB0000",
                        },
                    )
                ]

        async def _fake_merge(*_args, **_kwargs):
            return 0, 0, 0

        self.coordinator.discovery_module = True
        self.coordinator.discovery_module_address = "3412"
        self.discovery._module_type = "switch_module"
        self.discovery._decoders = [FakeDecoder()]

        message = "$2E1234ABCD"
        with patch(
            "nikobus_connect.discovery.discovery.merge_discovered_links",
            _fake_merge,
        ):
            await self.discovery.parse_module_inventory_response(message)
            self.discovery._cancel_timeout()

        mapping = self.discovery._decoded_buffer["command_mapping"]
        # Key is (push_button_address, key_raw, ir_code) — match on first two components
        matching_key = next(
            (k for k in mapping if k[0] == "AA0000" and k[1] == 1), None
        )
        self.assertIsNotNone(matching_key, f"Expected key starting with ('AA0000', 1) in {list(mapping)}")
        output = mapping[matching_key][0]
        self.assertEqual(output["module_address"], "3412")


class TestShutterDecoder(unittest.TestCase):
    """Verify the shutter decoder extracts channel from the lower nibble of byte 1.

    Real bus frame from roller module 8394:
        chunk = 723610B010FF → reversed = FF10B0103672
        byte 1 = 0x10 → upper nibble (key) = 1, lower nibble (channel_raw) = 0
        channel = (0 // 2) + 1 = 1
    """

    def _decode_chunk(self, chunk_hex: str, channel_count: int = 6) -> dict | None:
        payload_hex = reverse_hex(chunk_hex)
        raw_bytes = normalize_payload(payload_hex)
        ctx = DecoderContext(coordinator=None, module_address="8394", module_channel_count=channel_count)
        return shutter_decode(payload_hex, raw_bytes, ctx)

    def test_real_roller_payload_channel_1(self):
        result = self._decode_chunk("723610B010FF")
        self.assertIsNotNone(result, "Decoder should not reject a valid roller payload")
        self.assertEqual(result["channel"], 1)
        self.assertEqual(result["key_raw"], 1)
        self.assertEqual(result["M"], "M01 (Open - stop - close)")

    def test_channel_extraction_all_channels(self):
        """Ensure channels 1-6 are correctly decoded from lower nibble values 0-10."""
        # Build minimal valid payloads varying only the channel nibble in byte 1
        # Reversed payload: [t2_byte, key_ch_byte, t1_mode_byte, addr, addr, addr]
        for channel_nibble, expected_channel in [(0, 1), (2, 2), (4, 3), (6, 4), (8, 5), (0xA, 6)]:
            key = 1
            byte1 = (key << 4) | channel_nibble
            # Build reversed payload: FF <byte1> 00 00 00 00 (mode=0 → M01, t1=0, dummy addr)
            payload_hex = f"FF{byte1:02X}00000000"
            chunk_hex = reverse_hex(payload_hex)
            result = self._decode_chunk(chunk_hex)
            self.assertIsNotNone(result, f"channel_nibble={channel_nibble:#x} should decode")
            self.assertEqual(result["channel"], expected_channel, f"channel_nibble={channel_nibble:#x}")

    def test_empty_slot_skipped(self):
        result = self._decode_chunk("FFFFFFFFFFFF")
        self.assertIsNone(result)
