"""Lightweight helpers and routing for Nikobus discovery decoding."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from .mapping import CHANNEL_MAPPING, KEY_MAPPING_MODULE

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DecoderContext:
    """Shared context passed to all decoder functions."""

    coordinator: any
    module_address: str | None
    module_channel_count: int | None


def reverse_hex(hex_str: str) -> str:
    """Reverse the bytes in a hex string and return as upper-case hex."""

    b = bytes.fromhex(hex_str)
    reversed_b = b[::-1]
    return reversed_b.hex().upper()


def normalize_payload(payload_hex: str) -> list[str] | None:
    """Normalize payload into a list of hex byte strings."""

    try:
        payload_bytes = bytes.fromhex(payload_hex)
    except ValueError:
        _LOGGER.error("Invalid payload hex: %s", payload_hex)
        return None

    return [f"{byte:02X}" for byte in payload_bytes]


def _is_all_ff(payload_hex: str, expected_length: int | None = None) -> bool:
    """Return True when the payload is entirely the filler value 0xFF."""

    normalized = payload_hex.upper()
    if expected_length is not None and len(normalized) != expected_length:
        return False
    return bool(normalized) and set(normalized) == {"F"}


def _safe_int(hex_byte: str | None) -> int | None:
    """Safely convert a two-character hex byte to int."""

    if hex_byte is None:
        return None
    try:
        return int(hex_byte, 16)
    except (TypeError, ValueError):
        return None


def _format_channel(channel_number: int | None) -> str | None:
    """Return a consistent channel label for discovery logs."""

    if channel_number is None or channel_number <= 0:
        return None

    index = channel_number - 1
    return CHANNEL_MAPPING.get(index, f"Channel {channel_number}")


def classify_device_type(device_type_hex: str, device_types: dict) -> dict:
    """Return device metadata for the given device type."""

    normalized_type = (device_type_hex or "").strip().upper()
    return device_types.get(
        normalized_type,
        {"Category": "Unknown", "Name": "Unknown", "Model": "N/A", "Channels": 0},
    )


def convert_nikobus_address(address_string: str) -> str:
    """Convert a hex address string to a Nikobus address."""

    try:
        address = int(address_string, 16)
        nikobus_address = 0
        for i in range(21):
            nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)
        nikobus_address <<= 1
        button = (address >> 21) & 0x07
        final_address = nikobus_address + button
        return f"{final_address:06X}"
    except ValueError:
        return f"[{address_string}]"


def get_button_address(payload_hex: str) -> str | None:
    """Convert the 3-byte payload suffix into a button address."""

    try:
        bin_str = format(int(payload_hex, 16), "024b")
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.error("Error converting button address to binary: %s", err)
        return None

    modified = bin_str[:4] + bin_str[4:6] + bin_str[8:]
    group1 = modified[:6]
    group2 = modified[6:14]
    group3 = modified[14:]
    new_bin = group3 + group2 + group1
    try:
        result_int = int(new_bin, 2)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.error("Error converting binary to int: %s", err)
        return None
    return format(result_int, "06X")


def get_push_button_address(
    key_index: int | None,
    button_address: str | None,
    coordinator_get_button_channels,
    convert_func: Callable[[str], str] = convert_nikobus_address,
):
    """Return the derived push button address when possible."""

    if key_index is None or button_address is None:
        return None, button_address

    num_channels = None
    if coordinator_get_button_channels:
        try:
            num_channels = coordinator_get_button_channels(button_address)
        except Exception:  # pragma: no cover - defensive
            num_channels = None

    if num_channels is None or num_channels not in KEY_MAPPING_MODULE:
        return None, button_address

    mapping = KEY_MAPPING_MODULE[num_channels]
    if key_index not in mapping:
        return None, button_address

    push_button_address = convert_func(button_address)
    add_value = int(mapping[key_index], 16)
    try:
        original_nibble = int(push_button_address[0], 16)
    except Exception:
        return None, button_address

    new_nibble_value = original_nibble + add_value
    new_nibble_hex = f"{new_nibble_value:X}"
    final_push_button_address = new_nibble_hex + push_button_address[1:]

    return final_push_button_address, button_address


def decode_command_payload(
    payload_hex: str,
    module_type: str,
    coordinator,
    *,
    module_address: str | None = None,
    reverse_before_decode: bool = False,
    raw_chunk_hex: str | None = None,
    module_channel_count: int | None = None,
):
    """Decode a command payload using the module-specific decoder."""

    payload_hex = (payload_hex or "").strip().upper()
    raw_input = raw_chunk_hex or payload_hex

    if reverse_before_decode:
        payload_hex = reverse_hex(payload_hex)

    raw_bytes = normalize_payload(payload_hex)
    if raw_bytes is None:
        return None

    resolved_channel_count: int | None = module_channel_count
    if coordinator and module_address and resolved_channel_count is None:
        try:
            resolved_channel_count = coordinator.get_module_channel_count(module_address)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug(
                "Module channel lookup failed | module=%s error=%s", module_address, err
            )

    context = DecoderContext(
        coordinator=coordinator,
        module_address=module_address,
        module_channel_count=resolved_channel_count,
    )

    if module_type == "switch_module":
        from . import switch_decoder as decoder_module
    elif module_type == "roller_module":
        from . import shutter_decoder as decoder_module
    elif module_type == "dimmer_module":
        from . import dimmer_decoder as decoder_module
    else:
        decoder_module = None

    decoder = getattr(decoder_module, "decode", None) if decoder_module else None
    if decoder is None:
        _LOGGER.error("Unknown module_type '%s' for payload %s", module_type, raw_input)
        return None

    try:
        return decoder(payload_hex, raw_bytes, context)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.error(
            "Decoder error | type=%s module=%s payload=%s error=%s",
            module_type,
            module_address,
            payload_hex,
            err,
        )
        return None


__all__ = [
    "DecoderContext",
    "decode_command_payload",
    "normalize_payload",
    "reverse_hex",
    "convert_nikobus_address",
    "classify_device_type",
    "get_button_address",
    "get_push_button_address",
    "_format_channel",
    "_is_all_ff",
    "_safe_int",
]

