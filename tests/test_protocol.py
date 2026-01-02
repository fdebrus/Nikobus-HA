import logging
import pytest

from custom_components.nikobus.discovery.discovery import add_to_command_mapping
from custom_components.nikobus.discovery.mapping import (
    CHANNEL_MAPPING,
    DIMMER_MODE_MAPPING,
    DIMMER_TIMER_MAPPING,
    KEY_MAPPING_MODULE,
    SWITCH_MODE_MAPPING,
    SWITCH_TIMER_MAPPING,
)
from custom_components.nikobus.discovery.protocol import (
    convert_nikobus_address,
    decode_command_payload,
    _DIMMER_CANDIDATE_SUCCESS,
)


MODE_MAPPINGS = {
    "switch_module": SWITCH_MODE_MAPPING,
    "dimmer_module": DIMMER_MODE_MAPPING,
}

TIMER_MAPPINGS = {
    "switch_module": SWITCH_TIMER_MAPPING,
    "dimmer_module": DIMMER_TIMER_MAPPING,
}


def _get_channels(_):
    return 4


def _get_eight_channels(_):
    return 8


@pytest.fixture(autouse=True)
def reset_candidate_success():
    _DIMMER_CANDIDATE_SUCCESS.clear()
    yield
    _DIMMER_CANDIDATE_SUCCESS.clear()


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
        "001200000001",
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
    )

    mapping = {}
    assert first is not None
    assert second is not None
    add_to_command_mapping(mapping, first, "C9A5")
    add_to_command_mapping(mapping, second, "C9A5")
    add_to_command_mapping(mapping, first, "C9A5")

    key = (first["push_button_address"], first["key_raw"])
    assert key in mapping
    assert len(mapping[key]) == 2
    assert mapping[key][0]["channel"] == 1
    assert mapping[key][1]["channel"] == 2


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

    assert decoded is None
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warnings


def test_dimmer_decoder_uses_byte_based_candidates():
    payload = "0BB4021305787234"

    decoded = decode_command_payload(
        payload,
        "dimmer_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_eight_channels,
        convert_nikobus_address,
    )

    assert decoded is not None
    assert decoded["key_raw"] in range(0, 8)
    assert decoded["channel_raw"] in range(0, 12)
    assert decoded["mode_raw"] in DIMMER_MODE_MAPPING
    assert decoded["push_button_address"] is not None


def test_normalizes_prefixed_dimmer_payload():
    payload = "FF08F4AC5440"

    decoded = decode_command_payload(
        payload,
        "dimmer_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_eight_channels,
        convert_nikobus_address,
    )

    assert decoded is not None
    assert decoded["key_raw"] in range(0, 8)
    assert decoded["channel_raw"] in range(0, 8)


def test_non_dimmer_payload_unchanged_after_normalization():
    payload = "001000000001"

    decoded = decode_command_payload(
        payload,
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


def test_validation_rejects_invalid_channel(caplog):
    caplog.set_level(logging.WARNING)

    payload = "009909000001"
    decoded = decode_command_payload(
        payload,
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
    )

    assert decoded is None
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings


def test_raw_chunk_channel_bitmask_resolves_within_range():
    payload = "080835987A74"

    decoded = decode_command_payload(
        payload,
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_channels,
        convert_nikobus_address,
        reverse_before_decode=True,
        raw_chunk_hex=payload,
    )

    assert decoded is not None
    assert decoded["channel_raw"] in range(_get_channels(None))
    assert decoded.get("channel_mask") is not None


def test_raw_chunk_channel_bitmask_supports_eight_channel_modules():
    payload = "080835987A74"

    decoded = decode_command_payload(
        payload,
        "switch_module",
        KEY_MAPPING_MODULE,
        CHANNEL_MAPPING,
        MODE_MAPPINGS,
        TIMER_MAPPINGS,
        _get_eight_channels,
        convert_nikobus_address,
        reverse_before_decode=True,
        raw_chunk_hex=payload,
    )

    assert decoded is not None
    assert decoded["channel_raw"] in range(_get_eight_channels(None))
