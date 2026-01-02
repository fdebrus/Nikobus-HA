from __future__ import annotations

import logging
from typing import Any

from ..mapping import CHANNEL_MAPPING, DIMMER_MODE_MAPPING, DIMMER_TIMER_MAPPING, KEY_MAPPING_MODULE
from ..protocol import (
    _build_dimmer_candidates,
    _calculate_timer_values,
    convert_nikobus_address,
    get_button_address,
    get_push_button_address,
    reverse_hex,
)
from .base import DecodedCommand

_LOGGER = logging.getLogger(__name__)

EXPECTED_CHUNK_LEN = 16


class DimmerDecoder:
    module_type = "dimmer_module"

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def can_handle(self, module_type: str) -> bool:
        return module_type == self.module_type

    def _decode_fields(self, payload_hex: str) -> dict[str, Any] | None:
        raw_bytes = [payload_hex[i : i + 2] for i in range(0, len(payload_hex), 2)]
        button_address_hex = payload_hex[-6:]
        button_address = get_button_address(button_address_hex)
        num_channels = self._coordinator.get_button_channels(button_address)

        candidates = _build_dimmer_candidates(
            raw_bytes, CHANNEL_MAPPING, KEY_MAPPING_MODULE, DIMMER_MODE_MAPPING, num_channels
        )
        passing = [c for c in candidates if c["valid_all"]]
        if not passing and candidates:
            passing = sorted(
                candidates,
                key=lambda c: (int(c["valid_key"]), int(c["valid_channel"]), int(c["valid_mode"])),
                reverse=True,
            )[:1]

        if not passing:
            _LOGGER.error("No valid candidate interpretations for dimmer payload: %s", payload_hex)
            return None

        selected = max(passing, key=lambda c: c.get("count", 0))
        key_raw = selected.get("key_raw")
        channel_raw = selected.get("channel_raw")
        mode_raw = selected.get("mode_raw")
        t1_raw = selected.get("t1_raw")
        t2_raw = selected.get("t2_raw")

        push_button_address, normalized_button = get_push_button_address(
            key_raw,
            button_address,
            KEY_MAPPING_MODULE,
            self._coordinator.get_button_channels,
            convert_nikobus_address,
        )

        channel_label = CHANNEL_MAPPING.get(channel_raw, f"Unknown Channel ({channel_raw})")
        mode_label = DIMMER_MODE_MAPPING.get(mode_raw, f"Unknown Mode ({mode_raw})")

        t1_val, t2_val = _calculate_timer_values(
            "dimmer_module", mode_raw, t1_raw, t2_raw, {"dimmer_module": DIMMER_TIMER_MAPPING}
        )

        return {
            "payload": payload_hex,
            "button_address": normalized_button,
            "push_button_address": push_button_address,
            "key_raw": key_raw,
            "channel_raw": channel_raw,
            "mode_raw": mode_raw,
            "t1_raw": t1_raw,
            "t2_raw": t2_raw,
            "K": f"{key_raw}",
            "C": f"{channel_label}",
            "T1": t1_val,
            "T2": t2_val,
            "M": f"{mode_label}",
            "raw_bytes": raw_bytes,
        }

    def _chunk_from_message(self, message: str) -> tuple[str | None, str | None, str | None]:
        from ..const import DEVICE_INVENTORY

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
            _LOGGER.debug("Dimmer decoder unable to extract chunk from message: %s", message)
            return []

        chunk_hex = chunk_hex.upper()
        if len(chunk_hex) != EXPECTED_CHUNK_LEN:
            _LOGGER.error(
                "Dimmer chunk extraction failed | chunk=%s len=%s expected=%s",
                chunk_hex,
                len(chunk_hex),
                EXPECTED_CHUNK_LEN,
            )
            return []

        payload_hex = reverse_hex(chunk_hex)
        if len(payload_hex) != EXPECTED_CHUNK_LEN:
            _LOGGER.error(
                "Dimmer payload reversal mismatch | payload=%s len=%s expected=%s",
                payload_hex,
                len(payload_hex),
                EXPECTED_CHUNK_LEN,
            )
            return []

        decoded_fields = self._decode_fields(payload_hex)
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

        _LOGGER.debug(
            "Dimmer decoded chunk | address=%s chunk_len=%s payload_len=%s payload=%s",
            address,
            len(chunk_hex),
            len(payload_hex),
            payload_hex,
        )

        return [command]
