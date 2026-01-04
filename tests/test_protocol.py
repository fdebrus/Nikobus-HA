import logging

import pytest

from custom_components.nikobus.discovery.protocol import decode_command_payload


class DummyCoordinator:
    def __init__(self, channels: int = 4):
        self._channels = channels

    def get_module_channel_count(self, module_address: str | None) -> int:
        return self._channels

    def get_button_channels(self, button_address: str) -> int:
        return 4


@pytest.mark.parametrize(
    "payload,module_type",
    [
        ("FFFFFFFFFFFF", "switch_module"),
        ("FFFFFFFFFFFF", "roller_module"),
        ("FFFFFFFFFFFFFFFF", "dimmer_module"),
    ],
)
def test_all_ff_payloads_are_skipped(payload, module_type):
    assert (
        decode_command_payload(payload, module_type, DummyCoordinator(), module_address="C9A5")
        is None
    )


def test_switch_channel_is_decoded_with_offset():
    decoded = decode_command_payload(
        "001000000001", "switch_module", DummyCoordinator(channels=4), module_address="C9A5"
    )

    assert decoded is not None
    assert decoded["channel"] == 1
    assert decoded["M"].startswith("M01")


def test_switch_invalid_channel_is_rejected():
    decoded = decode_command_payload(
        "001500000001", "switch_module", DummyCoordinator(channels=2), module_address="C9A5"
    )

    assert decoded is None


def test_roller_channel_mapping_halves_output_space():
    decoded = decode_command_payload(
        "002001000001", "roller_module", DummyCoordinator(channels=3), module_address="C9A5"
    )

    assert decoded is not None
    assert decoded["channel"] == 1


def test_dimmer_decoding_uses_fixed_offsets():
    decoded = decode_command_payload(
        "0BB4021305787234", "dimmer_module", DummyCoordinator(channels=8), module_address="C9A5"
    )

    assert decoded is not None
    assert decoded["channel"] == 4
    assert decoded["key_raw"] == 1
