"""Chunk handling for switch and roller modules."""

from __future__ import annotations

import logging
from typing import Any

from .base import DecodedCommand
from .protocol import decode_command_payload, reverse_hex

_LOGGER = logging.getLogger(__name__)


_CRC_LEN = 6
_CHUNK_LENGTHS = {"switch_module": 12, "roller_module": 12}


class BaseChunkingDecoder:
    module_type: str

    def __init__(self, coordinator, module_type: str):
        self._coordinator = coordinator
        self.module_type = module_type
        self._module_address: str | None = None
        self._module_channel_count: int | None = None

    def can_handle(self, module_type: str) -> bool:
        return module_type == self.module_type

    def set_module_address(self, module_address: str | None) -> None:
        self._module_address = module_address

    def set_module_channel_count(self, module_channel_count: int | None) -> None:
        self._module_channel_count = module_channel_count

    def analyze_frame_payload(self, payload_buffer: str, payload_and_crc: str) -> dict[str, Any] | None:
        payload_and_crc = payload_and_crc.upper()
        if len(payload_and_crc) < _CRC_LEN:
            _LOGGER.debug(
                "Discovery skipped | type=%s reason=short_payload payload=%s",
                self.module_type,
                payload_and_crc,
            )
            return None

        data_region = payload_and_crc[: len(payload_and_crc) - _CRC_LEN]
        trailing_crc = payload_and_crc[len(payload_and_crc) - _CRC_LEN :]
        combined_payload = (payload_buffer + data_region).upper()

        expected_len = _CHUNK_LENGTHS.get(self.module_type)
        chunks: list[str] = []
        remainder = ""

        if expected_len:
            idx = 0
            while idx + expected_len <= len(combined_payload):
                chunk = combined_payload[idx : idx + expected_len]
                chunks.append(chunk)
                idx += expected_len
            remainder = combined_payload[idx:]

        return {
            "crc": trailing_crc,
            "payload_region": data_region,
            "chunks": chunks,
            "remainder": remainder,
        }

    def decode_chunk(self, chunk: str, module_address: str | None = None) -> list[DecodedCommand]:
        decoded = decode_command_payload(
            chunk,
            self.module_type,
            self._coordinator,
            module_address=module_address or self._module_address,
            reverse_before_decode=True,
            raw_chunk_hex=chunk,
            module_channel_count=self._module_channel_count,
        )

        if decoded is None:
            return []

        command = DecodedCommand(
            module_type=self.module_type,
            raw_message=chunk,
            prefix_hex=None,
            chunk_hex=chunk,
            payload_hex=reverse_hex(chunk),
            metadata=decoded,
        )
        return [command]

    def decode(self, message: str, module_address: str | None = None) -> list[DecodedCommand]:
        return self.decode_chunk(message.strip().upper(), module_address)


__all__ = ["BaseChunkingDecoder"]

