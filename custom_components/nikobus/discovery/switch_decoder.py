"""Deterministic switch module decoder."""

from __future__ import annotations

import logging
from typing import Any

from .chunk_decoder import BaseChunkingDecoder
from .mapping import SWITCH_MODE_MAPPING, SWITCH_TIMER_MAPPING
from .protocol import (
    _format_channel,
    _is_all_ff,
    _safe_int,
    get_button_address,
    get_push_button_address,
)

_LOGGER = logging.getLogger(__name__)


def _timer_value(mode_raw: int | None, t1_raw: int | None) -> tuple[str | None, str | None]:
    """Return timer values for switch modules based on deterministic mapping."""

    if mode_raw is None or t1_raw is None:
        return None, None

    timer_entry = SWITCH_TIMER_MAPPING.get(t1_raw, [None, None, None])
    if mode_raw in (5, 6):
        return timer_entry[0], None
    if mode_raw in (8, 9):
        return timer_entry[1], None
    if mode_raw in (1, 2):
        return timer_entry[2], None
    return None, None


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Decode a single switch payload into normalized fields."""

    if _is_all_ff(payload_hex, 12):
        _LOGGER.debug(
            "Discovery skipped | type=switch module=%s reason=empty_slot payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if len(raw_bytes) != 6:
        _LOGGER.debug(
            "Discovery skipped | type=switch module=%s reason=invalid_length payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    t2_raw = _safe_int(raw_bytes[0][1])
    key_raw = _safe_int(raw_bytes[1][0])
    channel_raw = _safe_int(raw_bytes[1][1])
    t1_raw = _safe_int(raw_bytes[2][0])
    mode_raw = _safe_int(raw_bytes[2][1])

    if None in (key_raw, channel_raw, mode_raw):
        _LOGGER.debug(
            "Discovery skipped | type=switch module=%s reason=invalid_length payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if mode_raw not in SWITCH_MODE_MAPPING:
        _LOGGER.debug(
            "Discovery skipped | type=switch module=%s reason=unknown_mode payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    channel_count = context.module_channel_count
    channel_decoded = channel_raw + 1 if channel_raw is not None else None
    if channel_count is not None and (
        channel_decoded is None or not (1 <= channel_decoded <= channel_count)
    ):
        _LOGGER.debug(
            "Discovery skipped | type=switch module=%s reason=invalid_channel payload=%s",
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

    t1_val, t2_val = _timer_value(mode_raw, t1_raw)

    decoded = {
        "payload": payload_hex,
        "button_address": normalized_button,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "channel": channel_decoded,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": t2_raw,
        "K": key_raw,
        "C": _format_channel(channel_decoded),
        "T1": t1_val,
        "T2": t2_val,
        "M": SWITCH_MODE_MAPPING.get(mode_raw),
    }

    _LOGGER.info(
        "Discovery decoded | type=switch module=%s button=%s key=%s channel=%s mode=%s",
        context.module_address,
        normalized_button,
        key_raw,
        decoded["channel"],
        decoded["M"],
    )

    return decoded


class SwitchDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "switch_module")


__all__ = ["SwitchDecoder", "decode"]

