from __future__ import annotations

import logging
from typing import Any

from ..mapping import CHANNEL_MAPPING, KEY_MAPPING_MODULE, SWITCH_MODE_MAPPING, SWITCH_TIMER_MAPPING
from ..protocol import convert_nikobus_address, decode_command_payload, reverse_hex
from .base import DecodedCommand

_LOGGER = logging.getLogger(__name__)


_CRC_CANDIDATES = (6, 8, 0)
_CHUNK_SIZE_CANDIDATES = (12, 16, 20, 24)
_SCORING_WEIGHTS = {
    "decode_success": 10.0,
    "decode_failure": 2.5,
    "expected_length_bonus": 3.0,
    "filler_penalty": 5.0,
    "reserved_pattern_penalty": 2.0,
    "key_plausible_bonus": 1.5,
    "channel_plausible_bonus": 1.5,
    "mode_present_bonus": 1.0,
    "remainder_penalty": 1.2,
    "crc_penalty": 0.5,
}


def _chunk_expected_lengths(module_type: str) -> int | None:
    return {
        "switch_module": 12,
        "roller_module": 12,
    }.get(module_type)


class BaseChunkingDecoder:
    module_type: str

    def __init__(self, coordinator, module_type: str):
        self._coordinator = coordinator
        self.module_type = module_type

    def can_handle(self, module_type: str) -> bool:
        return module_type == self.module_type

    def _score_chunk(self, chunk: str) -> tuple[float, dict[str, Any]]:
        chunk = chunk.strip().upper()
        reversed_chunk = reverse_hex(chunk)
        score = 0.0
        reasons: list[str] = []

        expected_len = _chunk_expected_lengths(self.module_type)
        if expected_len and len(chunk) == expected_len:
            score += _SCORING_WEIGHTS["expected_length_bonus"]
            reasons.append(f"matches_expected_len({expected_len})")

        bytes_pairs = [chunk[i : i + 2] for i in range(0, len(chunk), 2)]
        filler_ratio = sum(1 for b in bytes_pairs if b in {"FF", "00"}) / max(
            1, len(bytes_pairs)
        )
        if filler_ratio > 0.5:
            score -= _SCORING_WEIGHTS["filler_penalty"]
            reasons.append("filler_ratio")

        decoded: dict[str, Any] | None = None
        try:
            decoded = decode_command_payload(
                reversed_chunk,
                self.module_type,
                KEY_MAPPING_MODULE,
                CHANNEL_MAPPING,
                {"switch_module": SWITCH_MODE_MAPPING, "roller_module": SWITCH_MODE_MAPPING},
                {
                    "switch_module": SWITCH_TIMER_MAPPING,
                    "roller_module": SWITCH_TIMER_MAPPING,
                },
                self._coordinator.get_button_channels,
                convert_nikobus_address,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Decode error while scoring chunk %s: %s", chunk, err)

        if decoded:
            score += _SCORING_WEIGHTS["decode_success"]
            reasons.append("decoded")
            key_raw = decoded.get("key_raw")
            channel_raw = decoded.get("channel_raw")
            mode_raw = decoded.get("mode_raw")
            if isinstance(key_raw, int) and 0 <= key_raw <= 0x0F:
                score += _SCORING_WEIGHTS["key_plausible_bonus"]
                reasons.append("key_plausible")
            if isinstance(channel_raw, int) and 0 <= channel_raw <= 0x0F:
                score += _SCORING_WEIGHTS["channel_plausible_bonus"]
                reasons.append("channel_plausible")
            if mode_raw is not None:
                score += _SCORING_WEIGHTS["mode_present_bonus"]
        else:
            score -= _SCORING_WEIGHTS["decode_failure"]
            reasons.append("decode_failed")

        if filler_ratio > 0.75:
            score -= _SCORING_WEIGHTS["reserved_pattern_penalty"]
            reasons.append("reserved_pattern")

        return score, {
            "chunk": chunk,
            "reversed": reversed_chunk,
            "decoded": decoded,
            "score": score,
            "reasons": reasons,
        }

    def _solve_chunk_alignment(self, payload_hex: str) -> dict[str, Any]:
        payload_hex = payload_hex.upper()
        n = len(payload_hex)

        from functools import lru_cache

        @lru_cache(None)
        def _best_from(idx: int) -> tuple[float, list[str], list[dict[str, Any]]]:
            if idx >= n:
                return 0.0, [], []

            best_score = -float("inf")
            best_chunks: list[str] = []
            best_meta: list[dict[str, Any]] = []

            remainder_penalty = -_SCORING_WEIGHTS["remainder_penalty"] * ((n - idx) / 2)
            best_score = remainder_penalty

            for size in _CHUNK_SIZE_CANDIDATES:
                if idx + size > n:
                    continue
                chunk = payload_hex[idx : idx + size]
                chunk_score, chunk_meta = self._score_chunk(chunk)
                next_score, next_chunks, next_meta = _best_from(idx + size)
                total = chunk_score + next_score
                if total > best_score:
                    best_score = total
                    best_chunks = [chunk] + next_chunks
                    best_meta = [chunk_meta] + next_meta

            return best_score, best_chunks, best_meta

        score, chunks, meta = _best_from(0)
        consumed_length = sum(len(ch) for ch in chunks)
        remainder = payload_hex[consumed_length:]

        return {
            "score": score,
            "chunks": chunks,
            "meta": meta,
            "remainder": remainder,
        }

    def analyze_frame_payload(self, payload_buffer: str, payload_and_crc: str) -> dict[str, Any] | None:
        payload_and_crc = payload_and_crc.upper()
        best: dict[str, Any] | None = None
        for crc_len in _CRC_CANDIDATES:
            if crc_len < 0 or len(payload_and_crc) < crc_len:
                continue

            data_region = payload_and_crc[: len(payload_and_crc) - crc_len]
            trailing_crc = payload_and_crc[len(payload_and_crc) - crc_len :]

            combined_payload = (payload_buffer + data_region).upper()
            alignment = self._solve_chunk_alignment(combined_payload)
            total_score = alignment["score"]
            if alignment["remainder"]:
                total_score -= _SCORING_WEIGHTS["crc_penalty"]

            candidate = {
                "crc_len": crc_len,
                "crc": trailing_crc,
                "payload_region": data_region,
                "chunks": alignment["chunks"],
                "meta": alignment["meta"],
                "remainder": alignment["remainder"],
                "score": total_score,
            }

            _LOGGER.debug(
                "CRC candidate evaluated | crc_len=%s crc=%s chunks=%s score=%.2f remainder=%s",
                crc_len,
                trailing_crc,
                [len(ch) for ch in alignment["chunks"]],
                total_score,
                alignment["remainder"],
            )

            if best is None or candidate["score"] > best["score"]:
                best = candidate

        return best

    def decode(self, message: str) -> list[DecodedCommand]:
        chunk = message.strip().upper()
        reversed_chunk = reverse_hex(chunk)
        decoded = decode_command_payload(
            reversed_chunk,
            self.module_type,
            KEY_MAPPING_MODULE,
            CHANNEL_MAPPING,
            {"switch_module": SWITCH_MODE_MAPPING, "roller_module": SWITCH_MODE_MAPPING},
            {
                "switch_module": SWITCH_TIMER_MAPPING,
                "roller_module": SWITCH_TIMER_MAPPING,
            },
            self._coordinator.get_button_channels,
            convert_nikobus_address,
        )

        if decoded is None or decoded.get("push_button_address") is None:
            _LOGGER.debug("Skipped chunk during decode: %r", reversed_chunk)
            return []

        command = DecodedCommand(
            module_type=self.module_type,
            raw_message=message,
            prefix_hex=None,
            chunk_hex=chunk,
            payload_hex=reversed_chunk,
            metadata=decoded,
        )
        _LOGGER.debug("Decoded chunk: %r", reversed_chunk)
        return [command]


class SwitchDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "switch_module")


class ShutterDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "roller_module")
