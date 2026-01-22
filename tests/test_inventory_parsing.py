import importlib.util
import unittest
from unittest.mock import patch


HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None

if HA_AVAILABLE:
    from custom_components.nikobus.discovery.base import DecodedCommand, InventoryResult
    from custom_components.nikobus.discovery.discovery import NikobusDiscovery


def _build_inventory_payload(device_type: int, address_bytes: bytes, length: int = 18) -> str:
    payload = bytearray([0] * length)
    payload[7] = device_type
    payload[11 : 11 + len(address_bytes)] = address_bytes
    return payload.hex().upper()


@unittest.skipUnless(HA_AVAILABLE, "homeassistant not available")
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
        self.discovery = NikobusDiscovery(object(), self.coordinator)

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

        message = "$0510$2E1234ABCD"
        with patch(
            "custom_components.nikobus.discovery.discovery.merge_discovered_links",
            _fake_merge,
        ):
            await self.discovery.parse_module_inventory_response(message)
            self.discovery._cancel_timeout()

        mapping = self.discovery._decoded_buffer["command_mapping"]
        self.assertIn(("AA0000", 1), mapping)
        output = mapping[("AA0000", 1)][0]
        self.assertEqual(output["module_address"], "3412")
