"""Deterministic dimmer module decoder."""

from __future__ import annotations

import logging
from typing import Any

from ..const import DEVICE_INVENTORY
from .base import DecodedCommand
from .mapping import DIMMER_MODE_MAPPING
from .protocol import (
    _format_channel,
    _is_all_ff,
    _safe_int,
    decode_command_payload,
    get_button_address,
    get_push_button_address,
    reverse_hex,
)

_LOGGER = logging.getLogger(__name__)

EXPECTED_CHUNK_LEN = 16


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Decode a dimmer payload using fixed offsets (no heuristics)."""

    if _is_all_ff(payload_hex, EXPECTED_CHUNK_LEN):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=empty_slot payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if len(raw_bytes) != 8:
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=invalid_length payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    key_raw = _safe_int(raw_bytes[3][0])
    channel_raw = _safe_int(raw_bytes[3][1])
    mode_raw = _safe_int(raw_bytes[4][1])

    if None in (key_raw, channel_raw, mode_raw):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=invalid_length payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if mode_raw not in DIMMER_MODE_MAPPING:
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=unknown_mode payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    channel_decoded = channel_raw + 1 if channel_raw is not None else None
    channel_count = context.module_channel_count
    if channel_count is not None and (
        channel_decoded is None or not (1 <= channel_decoded <= channel_count)
    ):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=invalid_channel payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    button_address = get_button_address(payload_hex[-6:])
    push_button_address, normalized_button = get_push_button_address(
        key_raw,
        button_address,
        getattr(context.coordinator, "get_button_channels", None),
    )

    decoded = {
        "payload": payload_hex,
        "button_address": normalized_button,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "channel": channel_decoded,
        "mode_raw": mode_raw,
        "t1_raw": None,
        "t2_raw": None,
        "K": key_raw,
        "C": _format_channel(channel_decoded),
        "T1": None,
        "T2": None,
        "M": DIMMER_MODE_MAPPING.get(mode_raw),
    }

    _LOGGER.debug(
        "Discovery decoded | type=dimmer module=%s button=%s key=%s channel=%s mode=%s",
        context.module_address,
        normalized_button,
        key_raw,
        decoded["channel"],
        decoded["M"],
    )

    return decoded


class DimmerDecoder:
    module_type = "dimmer_module"

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def can_handle(self, module_type: str) -> bool:
        return module_type == self.module_type

    def _chunk_from_message(self, message: str) -> tuple[str | None, str | None, str | None]:
        matched_header = next((candidate for candidate in DEVICE_INVENTORY if message.startswith(candidate)), None)
        if matched_header is None:
            return None, None, None

        header_suffix = matched_header.split("$")[-1]
        frame_body = message[len(matched_header) :]
        if len(frame_body) < 4:
            return header_suffix, None, None

        address = (header_suffix + frame_body[:4]).upper()
        payload_and_crc = frame_body[4:]
        return address, payload_and_crc[:EXPECTED_CHUNK_LEN], payload_and_crc[EXPECTED_CHUNK_LEN:]

    def decode(self, message: str) -> list[DecodedCommand]:
        message = message.strip()
        address, chunk_hex, crc_region = self._chunk_from_message(message)

        if chunk_hex is None or address is None:
            _LOGGER.debug("Discovery skipped | type=dimmer module=%s payload=%s reason=invalid_length", address, message)
            return []

        chunk_hex = chunk_hex.upper()
        if len(chunk_hex) != EXPECTED_CHUNK_LEN:
            _LOGGER.debug(
                "Discovery skipped | type=dimmer module=%s payload=%s reason=invalid_length",
                address,
                chunk_hex,
            )
            return []

        payload_hex = reverse_hex(chunk_hex)
        if _is_all_ff(payload_hex, EXPECTED_CHUNK_LEN):
            _LOGGER.debug(
                "Discovery skipped | type=dimmer module=%s payload=%s reason=empty_slot",
                address,
                payload_hex,
            )
            return []

        decoded_fields = decode_command_payload(
            payload_hex,
            self.module_type,
            self._coordinator,
            module_address=address,
            reverse_before_decode=False,
            raw_chunk_hex=chunk_hex,
        )

        if decoded_fields is None:
            return []

        decoded_fields.update({"crc_region": crc_region, "module_address": address})

        command = DecodedCommand(
            module_type=self.module_type,
            raw_message=message,
            prefix_hex=address,
            chunk_hex=chunk_hex,
            payload_hex=payload_hex,
            metadata=decoded_fields,
        )

        return [command]


__all__ = ["DimmerDecoder", "decode", "EXPECTED_CHUNK_LEN"]

