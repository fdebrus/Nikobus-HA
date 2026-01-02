import logging
import pytest

from custom_components.nikobus.discovery.discovery import add_to_command_mapping
from custom_components.nikobus.discovery.mapping import (
    CHANNEL_MAPPING,
    KEY_MAPPING_MODULE,
    SWITCH_MODE_MAPPING,
    SWITCH_TIMER_MAPPING,
)
from custom_components.nikobus.discovery.protocol import (
    convert_nikobus_address,
    decode_command_payload,
)


MODE_MAPPINGS = {
    "switch_module": SWITCH_MODE_MAPPING,
}

TIMER_MAPPINGS = {
    "switch_module": SWITCH_TIMER_MAPPING,
}


def _get_channels(_):
    return 4


def test_decode_skips_terminator_and_filler_records():
    assert (
        decode_command_payload(
            "FFFFFFFFFFFF",
            "switch_module",
            KEY_MAPPING_MODULE,
            CHANNEL_MAPPING,
            MODE_MAPPINGS,
            TIMER_MAPPINGS,
            _get_channels,
            convert_nikobus_address,
        )
        is None
    )

    assert (
        decode_command_payload(
            "00F000000001",
            "switch_module",
            KEY_MAPPING_MODULE,
            CHANNEL_MAPPING,
            MODE_MAPPINGS,
            TIMER_MAPPINGS,
            _get_channels,
            convert_nikobus_address,
        )
        is None
    )

    assert (
        decode_command_payload(
            "001000FFFFFF",
            "switch_module",
            KEY_MAPPING_MODULE,
            CHANNEL_MAPPING,
            MODE_MAPPINGS,
            TIMER_MAPPINGS,
            _get_channels,
            convert_nikobus_address,
        )
        is None
    )


def test_decode_valid_record():
    decoded = decode_command_payload(
        "001000000001",
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
    )

    assert decoded is not None
    assert decoded["key_raw"] == 1
    assert decoded["channel_raw"] == 0
    assert decoded["push_button_address"] is not None


def test_command_mapping_supports_one_to_many_and_deduplication():
    first = decode_command_payload(
        "001000000001",
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
    )

    second = decode_command_payload(
        "001100000001",
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
    )

    mapping = {}
    add_to_command_mapping(mapping, first, "C9A5")
    add_to_command_mapping(mapping, second, "C9A5")
    add_to_command_mapping(mapping, first, "C9A5")

    key = (first["push_button_address"], first["key_raw"])
    assert key in mapping
    assert len(mapping[key]) == 2
    assert mapping[key][0]["channel"] == 0
    assert mapping[key][1]["channel"] == 1


def test_decode_handles_reversed_and_missing_mappings(caplog):
    caplog.set_level(logging.DEBUG)

    assert (
        decode_command_payload(
            "FFFFFFFFFF08",
            "switch_module",
            KEY_MAPPING_MODULE,
            CHANNEL_MAPPING,
            MODE_MAPPINGS,
            TIMER_MAPPINGS,
            _get_channels,
            convert_nikobus_address,
        )
        is None
    )

    assert (
        decode_command_payload(
            "00F000000001",
            "switch_module",
            KEY_MAPPING_MODULE,
            CHANNEL_MAPPING,
            MODE_MAPPINGS,
            TIMER_MAPPINGS,
            _get_channels,
            convert_nikobus_address,
        )
        is None
    )

    caplog.clear()
    decoded = decode_command_payload(
        "00D000000001",
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
    )

    assert decoded is not None
    assert decoded["push_button_address"] is None
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == []
