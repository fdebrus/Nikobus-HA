import asyncio

import pytest

from custom_components.nikobus.discovery.base import DecodedCommand
from custom_components.nikobus.discovery.discovery import NikobusDiscovery
from custom_components.nikobus.discovery.switch_decoder import SwitchDecoder


class DummyCommandQueue:
    async def clear_command_queue(self):  # pragma: no cover - not used in these tests
        return None

    async def clear_inventory_commands_for_prefix(self, _):  # pragma: no cover - test stub
        return None


class DummyCoordinator:
    def __init__(self):
        self.discovery_running = False
        self.discovery_module = False
        self.discovery_module_address = None
        self.nikobus_command = DummyCommandQueue()

    def get_button_channels(self, _):
        return 8

    def get_module_type(self, _):  # pragma: no cover - helper for completeness
        return None


@pytest.mark.parametrize(
    "message, module_type, expected_chunk",
    [
        (
            "$0522$1E6C0E5F1550000300B4FF452CA9",
            "dimmer_module",
            ["5F1550000300B4FF"],
        ),
        (
            "$0522$1EABCD73EE80053000B412ABCDEF",
            "dimmer_module",
            ["73EE80053000B412"],
        ),
        (
            "$0522$1E000177C958022BFF112233",
            "switch_module",
            ["77C958022BFF"],
        ),
    ],
)
def test_chunk_alignment_does_not_swallow_crc(message, module_type, expected_chunk):
    coordinator = DummyCoordinator()
    discovery = NikobusDiscovery(None, coordinator)
    discovery._module_type = module_type

    matched_header = message.split("$")[1]
    matched_header = f"${matched_header}${message.split('$')[2][:2]}"  # rebuild like DEVICE_INVENTORY entries
    header_suffix = matched_header.split("$")[-1]
    frame_body = message[len(matched_header) :]

    address = (header_suffix + frame_body[:4]).upper()
    payload_and_crc = frame_body[4:]

    analysis = discovery._analyze_frame_payload(address, payload_and_crc)

    assert analysis is not None
    assert analysis["chunks"] == expected_chunk
    assert not analysis["remainder"]
    if analysis["crc_len"]:
        assert analysis["crc"] == payload_and_crc[-analysis["crc_len"] :]


def test_termination_chunk_triggers_completion(monkeypatch):
    coordinator = DummyCoordinator()
    coordinator.get_module_type = lambda _: "switch_module"

    clear_called = False

    async def _clear_queue():
        nonlocal clear_called
        clear_called = True

    coordinator.nikobus_command.clear_command_queue = _clear_queue

    decoded_chunks: list[str] = []

    def fake_decode(self, message):  # pragma: no cover - simple harness stub
        decoded_chunks.append(message.upper())
        return [
            DecodedCommand(
                module_type=self.module_type,
                raw_message=message,
                chunk_hex=message,
                payload_hex=message,
                metadata={"push_button_address": "PB", "payload": message},
            )
        ]

    monkeypatch.setattr(SwitchDecoder, "decode", fake_decode)

    discovery = NikobusDiscovery(None, coordinator)

    frame_chunks = ["112233445566", "AABBCCDDEEFF", "FFFFFFFFFFFF"]
    crc = "ABCDEF"
    payload_and_crc = "".join(frame_chunks) + crc
    message = f"$0510$2E1234{payload_and_crc}"

    asyncio.run(discovery.parse_module_inventory_response(message))

    assert clear_called
    assert decoded_chunks == frame_chunks[:2]


def test_module_address_reversed_without_header(monkeypatch):
    coordinator = DummyCoordinator()
    coordinator.get_module_type = lambda _: "switch_module"

    captured_address = None

    async def fake_handle(self, module_address, decoded_commands):  # pragma: no cover - stub
        nonlocal captured_address
        captured_address = module_address

    def fake_decode(self, message):  # pragma: no cover - stub
        return [
            DecodedCommand(
                module_type="switch_module",
                raw_message=message,
                chunk_hex=message,
                payload_hex=message,
                metadata={
                    "push_button_address": "PB",
                    "payload": message,
                    "key_raw": 1,
                    "channel_raw": 1,
                    "M": "mode",
                    "T1": "t1",
                    "T2": "t2",
                    "button_address": "BA",
                },
            )
        ]

    monkeypatch.setattr(NikobusDiscovery, "_handle_decoded_commands", fake_handle)
    monkeypatch.setattr(SwitchDecoder, "decode", fake_decode)

    discovery = NikobusDiscovery(None, coordinator)

    chunk = "112233445566"
    termination = "FFFFFFFFFFFF"
    crc = "ABCDEF"
    message = f"$0510$2EA5C9{chunk}{termination}{crc}"

    asyncio.run(discovery.parse_module_inventory_response(message))

    assert captured_address == "C9A5"


def test_analyze_frame_payload_respects_termination():
    coordinator = DummyCoordinator()
    decoder = SwitchDecoder(coordinator)

    payload_buffer = ""
    frame_chunks = ["001122334455", "ABCDEF123456", "FFFFFFFFFFFF"]
    crc = "123ABC"
    payload_and_crc = "".join(frame_chunks) + crc

    analysis = decoder.analyze_frame_payload(payload_buffer, payload_and_crc)

    assert analysis is not None
    assert analysis["chunks"] == frame_chunks[:2]
    assert analysis["terminated"] is True
    assert analysis["remainder"] == ""


async def test_dimmer_empty_responses_trigger_early_stop(monkeypatch):
    cleared_prefix: list[str | None] = []

    class _CommandQueue(DummyCommandQueue):
        async def clear_inventory_commands_for_prefix(self, prefix):
            cleared_prefix.append(prefix)

    coordinator = DummyCoordinator()
    coordinator.get_module_type = lambda _: "dimmer_module"
    coordinator.nikobus_command = _CommandQueue()

    monkeypatch.setattr(
        "custom_components.nikobus.discovery.discovery.DIMMER_EMPTY_RESPONSE_THRESHOLD",
        2,
    )
    monkeypatch.setattr(
        "custom_components.nikobus.discovery.dimmer_decoder.DimmerDecoder.decode",
        lambda self, message: [],
    )

    discovery = NikobusDiscovery(None, coordinator)
    discovery._inventory_command_prefix = "22A1B2"
    discovery._inventory_commands_remaining = 2

    empty_message = "$0522$1E1234FFFFFFFFFFFFFFFF"

    await discovery.parse_module_inventory_response(empty_message)
    await discovery.parse_module_inventory_response(empty_message)

    assert cleared_prefix == ["22A1B2"]
    assert discovery._coordinator.discovery_module is False

