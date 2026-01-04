"""Deterministic roller shutter decoder."""

from __future__ import annotations

import logging
from typing import Any

from .base import DecodedCommand
from .chunk_decoder import BaseChunkingDecoder
from .mapping import ROLLER_MODE_MAPPING, ROLLER_TIMER_MAPPING
from .protocol import (
    _format_channel,
    _is_all_ff,
    _safe_int,
    decode_command_payload,
    get_button_address,
    get_push_button_address,
)

_LOGGER = logging.getLogger(__name__)


def _timer_value(t1_raw: int | None) -> tuple[str | None, str | None]:
    if t1_raw is None:
        return None, None
    timer_entry = ROLLER_TIMER_MAPPING.get(t1_raw)
    return timer_entry[0] if timer_entry else None, None


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Decode a shutter payload following the deterministic selector rule."""

    if _is_all_ff(payload_hex, 12):
        _LOGGER.debug(
            "Discovery skipped | type=roller module=%s payload=%s reason=empty_slot",
            context.module_address,
            payload_hex,
        )
        return None

    if len(raw_bytes) != 6:
        _LOGGER.debug(
            "Discovery skipped | type=roller module=%s payload=%s reason=invalid_length",
            context.module_address,
            payload_hex,
        )
        return None

    t2_raw = _safe_int(raw_bytes[0][1])
    key_raw = _safe_int(raw_bytes[1][0])
    selector_byte = _safe_int(raw_bytes[1])
    t1_raw = _safe_int(raw_bytes[2][0])
    mode_raw = _safe_int(raw_bytes[2][1])

    if None in (key_raw, selector_byte, mode_raw):
        _LOGGER.debug(
            "Discovery skipped | type=roller module=%s payload=%s reason=invalid_length",
            context.module_address,
            payload_hex,
        )
        return None

    if mode_raw not in ROLLER_MODE_MAPPING:
        _LOGGER.debug(
            "Discovery skipped | type=roller module=%s payload=%s reason=unknown_mode",
            context.module_address,
            payload_hex,
        )
        return None

    # Selector nibble encodes the motor line; flag bits (e.g., 0x80) are ignored.
    selector = selector_byte & 0x0F
    if selector % 2 == 1:
        _LOGGER.debug(
            "Discovery skipped | type=roller module=%s payload=%s reason=odd_selector",
            context.module_address,
            payload_hex,
        )
        return None

    channel_decoded = (selector // 2) + 1
    channel_count = context.module_channel_count
    if channel_count is not None and not (0 <= channel_decoded < channel_count):
        _LOGGER.debug(
            "Discovery skipped | type=roller module=%s payload=%s reason=invalid_channel",
            context.module_address,
            payload_hex,
        )
        return None

    channel_raw = channel_decoded
    button_address = get_button_address(payload_hex[-6:])
    push_button_address, normalized_button = get_push_button_address(
        key_raw,
        button_address,
        getattr(context.coordinator, "get_button_channels", None),
    )

    t1_val, t2_val = _timer_value(t1_raw)

    decoded = {
        "payload": payload_hex,
        "button_address": normalized_button,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": t2_raw,
        "K": key_raw,
        "C": _format_channel(channel_raw),
        "T1": t1_val,
        "T2": t2_val,
        "M": ROLLER_MODE_MAPPING.get(mode_raw),
    }

    _LOGGER.debug(
        "Discovery decoded | type=roller module=%s button=%s key=%s channel=%s mode=%s t1=%s t2=%s",
        context.module_address,
        normalized_button,
        key_raw,
        decoded["C"],
        decoded["M"],
        t1_val,
        t2_val,
    )

    return decoded


class ShutterDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "roller_module")

    def decode_chunk(self, chunk: str, module_address: str | None = None) -> list[DecodedCommand]:
        decoded = decode_command_payload(
            chunk,
            self.module_type,
            self._coordinator,
            module_address=module_address or self._module_address,
            logical_channel_count=self._logical_channel_count,
            reverse_before_decode=True,
            raw_chunk_hex=chunk,
        )

        if decoded is None:
            _LOGGER.debug(
                "Discovery skipped | type=roller module=%s payload=%s reason=empty_slot",
                module_address or self._module_address,
                chunk,
            )
            return []

        command = DecodedCommand(
            module_type=self.module_type,
            raw_message=chunk,
            prefix_hex=None,
            chunk_hex=chunk,
            payload_hex=chunk,
            metadata=decoded,
        )
        return [command]


__all__ = ["ShutterDecoder", "decode"]

