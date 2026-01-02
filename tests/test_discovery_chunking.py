import pytest

from custom_components.nikobus.discovery.discovery import NikobusDiscovery


class DummyCommandQueue:
    async def clear_command_queue(self):  # pragma: no cover - not used in these tests
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

