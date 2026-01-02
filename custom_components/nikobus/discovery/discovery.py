import asyncio
import logging

from .mapping import (
    DEVICE_TYPES,
    SWITCH_MODE_MAPPING,
    DIMMER_MODE_MAPPING,
    ROLLER_MODE_MAPPING,
    SWITCH_TIMER_MAPPING,
    DIMMER_TIMER_MAPPING,
    ROLLER_TIMER_MAPPING,
    KEY_MAPPING,
    KEY_MAPPING_MODULE,
    CHANNEL_MAPPING,
)
from .protocol import (
    reverse_hex,
    classify_device_type,
    convert_nikobus_address,
    decode_command_payload,
)
from ..const import DEVICE_INVENTORY
from .fileio import (
    merge_discovered_links,
    update_button_data,
    update_module_data,
)
from ..nkbprotocol import make_pc_link_inventory_command

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


def _chunk_expected_lengths(module_type):
    return {
        "switch_module": 12,
        "dimmer_module": 16,
        "roller_module": 12,
    }.get(module_type)


def add_to_command_mapping(command_mapping, decoded_command, module_address):
    """Store decoded command information, allowing one-to-many button mappings."""
    push_button_address = decoded_command.get("push_button_address")
    key_raw = decoded_command.get("key_raw")
    if push_button_address is None or key_raw is None:
        return

    mapping_key = (push_button_address, key_raw)
    outputs = command_mapping.setdefault(mapping_key, [])
    channel_raw = decoded_command.get("channel_raw")
    channel_number = channel_raw + 1 if isinstance(channel_raw, int) else None

    output_definition = {
        "module_address": module_address,
        "channel": channel_number,
        "mode": decoded_command.get("M"),
        "t1": decoded_command.get("T1"),
        "t2": decoded_command.get("T2"),
        "payload": decoded_command.get("payload"),
        "button_address": decoded_command.get("button_address"),
    }

    dedupe_key = (
        output_definition["module_address"],
        output_definition["channel"],
        output_definition["mode"],
        output_definition["t1"],
        output_definition["t2"],
    )
    existing_keys = {
        (
            entry.get("module_address"),
            entry.get("channel"),
            entry.get("mode"),
            entry.get("t1"),
            entry.get("t2"),
        )
        for entry in outputs
    }

    if dedupe_key not in existing_keys:
        outputs.append(output_definition)

class NikobusDiscovery:
    def __init__(self, hass, coordinator):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._hass = hass
        self._timeout_seconds = 5.0
        self.reset_state()

    def reset_state(self):
        self._payload_buffer = ""
        self._chunks = []
        self._module_address = None
        self._module_type = None
        self._message_complete = False
        self._timeout_task = None
        if hasattr(self._coordinator, "discovery_running"):
            self._coordinator.discovery_running = False
        if hasattr(self._coordinator, "discovery_module"):
            self._coordinator.discovery_module = False
        if hasattr(self._coordinator, "discovery_module_address"):
            self._coordinator.discovery_module_address = None

    def _score_chunk(self, chunk):
        chunk = chunk.strip().upper()
        reversed_chunk = reverse_hex(chunk)
        score = 0.0
        reasons = []

        expected_len = _chunk_expected_lengths(self._module_type)
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

        decoded = None
        try:
            decoded = decode_command_payload(
                reversed_chunk,
                self._module_type,
                KEY_MAPPING_MODULE,
                CHANNEL_MAPPING,
                {
                    "switch_module": SWITCH_MODE_MAPPING,
                    "dimmer_module": DIMMER_MODE_MAPPING,
                    "roller_module": ROLLER_MODE_MAPPING,
                },
                {
                    "switch_module": SWITCH_TIMER_MAPPING,
                    "dimmer_module": DIMMER_TIMER_MAPPING,
                    "roller_module": ROLLER_TIMER_MAPPING,
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

    def _solve_chunk_alignment(self, payload_hex):
        payload_hex = payload_hex.upper()
        n = len(payload_hex)

        from functools import lru_cache

        @lru_cache(None)
        def _best_from(idx):
            if idx >= n:
                return 0.0, [], []

            best_score = -float("inf")
            best_chunks = []
            best_meta = []

            remainder_penalty = -_SCORING_WEIGHTS["remainder_penalty"] * (
                (n - idx) / 2
            )
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

    def _analyze_frame_payload(self, address, payload_and_crc):
        address = address.upper()
        payload_and_crc = payload_and_crc.upper()
        best = None
        for crc_len in _CRC_CANDIDATES:
            if crc_len < 0 or len(payload_and_crc) < crc_len:
                continue

            data_region = payload_and_crc[: len(payload_and_crc) - crc_len]
            trailing_crc = payload_and_crc[len(payload_and_crc) - crc_len :]

            combined_payload = (self._payload_buffer + data_region).upper()
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

        if best is None:
            return None

        chunk_sizes = [len(c) for c in best["chunks"]]
        _LOGGER.debug(
            "Frame segmented | address=%s crc_len=%s crc=%s chunk_sizes=%s score=%.2f remainder=%s",
            address,
            best.get("crc_len"),
            best.get("crc"),
            chunk_sizes,
            best.get("score"),
            best.get("remainder"),
        )
        for entry in best.get("meta", []):
            _LOGGER.debug(
                "Chunk score detail | chunk=%s reversed=%s score=%.2f reasons=%s",
                entry.get("chunk"),
                entry.get("reversed"),
                entry.get("score"),
                ",".join(entry.get("reasons", [])),
            )

        return best

    def _cancel_timeout(self):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

    async def _timeout_waiter(self):
        try:
            await asyncio.sleep(self._timeout_seconds)
            if not self._message_complete:
                _LOGGER.debug("Timeout reached. Processing complete message.")
                await self.process_complete_message()
        except asyncio.CancelledError:
            pass

    async def query_module_inventory(self, device_address):
        if device_address == "ALL":
            all_addresses = self._coordinator.get_all_module_addresses()
            for addr in all_addresses:
                _LOGGER.info("Starting discovery for module: %s", addr)
                self._coordinator.discovery_running = True
                self._coordinator.discovery_module = True
                self._coordinator.discovery_module_address = addr
                await self.query_module_inventory(addr)
                while self._coordinator.discovery_module:
                    await asyncio.sleep(0.5)
                _LOGGER.info("Completed discovery for module: %s", addr)
            self.reset_state()
            if hasattr(self, "on_discovery_finished") and self.on_discovery_finished:
                await self.on_discovery_finished()
            return

        base_command = f"10{device_address}"
        self._module_address = device_address

        if self._coordinator.discovery_module:
            base_command = f"10{device_address[2:4] + device_address[:2]}"
            self._module_type = self._coordinator.get_module_type(device_address)
            if self._module_type == "dimmer_module":
                base_command = f"22{device_address[2:4] + device_address[:2]}"
                command_range = range(0x20, 0x100)  # 0x20 à 0xFF inclus, un à un
            else:
                command_range = range(0x10, 0x100)  # 0x10 à 0xFF inclus, un à un
        else:
            command_range = range(0xA4, 0x100)      # 0xA0 à 0xFF inclus

        for cmd in command_range:
            partial_hex = f"{base_command}{cmd:02X}04"
            pc_link_command = make_pc_link_inventory_command(partial_hex)
            await self._coordinator.nikobus_command.queue_command(pc_link_command)

    async def parse_inventory_response(self, payload):
        try:
            if payload.startswith("$0510$"):
                payload = payload[6:]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)
            data_bytes = payload_bytes[2:18] if len(payload_bytes) >= 18 else payload_bytes[2:]

            device_type_hex = f"{payload_bytes[7]:02X}"
            
            if device_type_hex == "FF":
                _LOGGER.debug("Empty register / device_address == FF")
                await self._coordinator.nikobus_command.clear_command_queue()
                self._message_complete = True
                self._cancel_timeout()
                self.reset_state()
                if hasattr(self, "on_discovery_finished") and self.on_discovery_finished:
                    await self.on_discovery_finished()
                return

            device_info = classify_device_type(device_type_hex, DEVICE_TYPES)
            category = device_info.get("Category", "Unknown")
            name = device_info.get("Name", "Unknown")
            model = device_info.get("Model", "N/A")
            channels = device_info.get("Channels", 0)
            slice_end = 13 if category == "Module" else 14
            converted_address = payload_bytes[11:slice_end][::-1].hex().upper()

            if category == "Unknown":
                _LOGGER.warning(
                    "Unknown device detected: Type %s at Address %s. "
                    "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                    device_type_hex,
                    converted_address,
                )
                return

            if converted_address not in self.discovered_devices:
                base_device = {
                    "description": name,
                    "category": category,
                    "model": model,
                    "address": converted_address,
                    "channels": channels,
                }
                self.discovered_devices[converted_address] = base_device

            _LOGGER.info(
                "Discovered %s - %s, Model: %s, at Address: %s",
                category,
                name,
                model,
                converted_address,
            )

            if category == "Module":
                await update_module_data(self._hass, self.discovered_devices)
            elif category == "Button":
                await update_button_data(
                    self._hass,
                    self.discovered_devices,
                    KEY_MAPPING,
                    convert_nikobus_address,
                )
        except Exception as e:
            _LOGGER.error("Failed to parse Nikobus payload: %s", e)
            self.reset_state()

    async def parse_module_inventory_response(self, message):
        try:
            _LOGGER.debug("Received message: %r", message)
            matched_header = next(
                (h for h in DEVICE_INVENTORY if message.startswith(h)), None
            )
            if not matched_header:
                _LOGGER.error("Message does not start with expected header.")
                return

            header_suffix = matched_header.split("$")[-1]
            frame_body = message[len(matched_header) :]

            if len(frame_body) < 4:
                _LOGGER.error("Frame body too short to contain address and payload.")
                return

            address = (header_suffix + frame_body[:4]).upper()
            payload_and_crc = frame_body[4:]

            analysis = self._analyze_frame_payload(address, payload_and_crc)
            if analysis is None:
                _LOGGER.error("Unable to analyze frame payload for message: %s", message)
                return

            self._module_address = address
            self._payload_buffer = analysis["remainder"]
            self._chunks.extend(analysis["chunks"])

            self._cancel_timeout()
            self._timeout_task = asyncio.create_task(self._timeout_waiter())

            for candidate_chunk in self._chunks:
                if candidate_chunk.strip().upper() == "F" * len(candidate_chunk):
                    _LOGGER.debug("Termination chunk encountered: %r", candidate_chunk)
                    await self._coordinator.nikobus_command.clear_command_queue()
                    self._message_complete = True
                    self._cancel_timeout()
                    await self.process_complete_message()
                    return

        except Exception as e:
            _LOGGER.error("Failed to parse module inventory response: %s", e)
            self.reset_state()

    async def process_complete_message(self):
        try:
            termination_index = None
            for i, chunk in enumerate(self._chunks):
                if chunk.strip().upper() == ("F" * len(chunk)):
                    termination_index = i
                    _LOGGER.debug("Termination chunk encountered at index %d.", i)
                    break

            if termination_index is not None:
                chunks_to_process = self._chunks[:termination_index]
            else:
                chunks_to_process = self._chunks
            self._chunks = []

            _LOGGER.debug(
                "Processing complete message with %d chunks.", len(chunks_to_process)
            )
            new_commands = []
            command_mapping = {}
            for chunk in chunks_to_process:
                _LOGGER.debug("Decoding chunk: %r", chunk)
                reversed_chunk = reverse_hex(chunk)
                decoded = decode_command_payload(
                    reversed_chunk,
                    self._module_type,
                    KEY_MAPPING_MODULE,
                    CHANNEL_MAPPING,
                    {
                        "switch_module": SWITCH_MODE_MAPPING,
                        "dimmer_module": DIMMER_MODE_MAPPING,
                        "roller_module": ROLLER_MODE_MAPPING,
                    },
                    {
                        "switch_module": SWITCH_TIMER_MAPPING,
                        "dimmer_module": DIMMER_TIMER_MAPPING,
                        "roller_module": ROLLER_TIMER_MAPPING,
                    },
                    self._coordinator.get_button_channels,
                    convert_nikobus_address,
                )
                if decoded is not None and decoded.get("push_button_address") is not None:
                    new_commands.append(decoded)
                    add_to_command_mapping(
                        command_mapping, decoded, self._module_address
                    )
                    _LOGGER.debug("Decoded chunk: %r", reversed_chunk)
                else:
                    _LOGGER.debug("Skipped chunk during decode: %r", reversed_chunk)

            self._decoded_buffer = {
                "module_address": self._module_address,
                "commands": new_commands,
                "command_mapping": command_mapping,
            }

            _LOGGER.info("Decoded Button Commands:")
            _LOGGER.info("module_address: %s", self._decoded_buffer["module_address"])
            for idx, cmd in enumerate(self._decoded_buffer["commands"], start=1):
                _LOGGER.info(
                    "Command %d: Payload: %s, Button Address: %s, Push Button Address: %s, Key: %s, Channel: %s, T1: %s, T2: %s, Mode: %s",
                    idx,
                    cmd.get("payload"),
                    cmd.get("button_address"),
                    cmd.get("push_button_address"),
                    cmd.get("K"),
                    cmd.get("C"),
                    cmd.get("T1"),
                    cmd.get("T2"),
                    cmd.get("M"),
                )

            updated_buttons, links_added, outputs_added = await merge_discovered_links(
                self._hass, command_mapping
            )
            _LOGGER.info(
                "Discovered links merged into config: %d buttons updated, %d link blocks added, %d outputs added.",
                updated_buttons,
                links_added,
                outputs_added,
            )

            self._payload_buffer = ""
            self._decoded_buffer = {"module_address": None, "commands": [], "command_mapping": {}}
            self._module_address = None
            self._message_complete = False

        except Exception as e:
            _LOGGER.error("Error during process_complete_message: %s", e)
        finally:
            self.reset_state()
            if hasattr(self, "on_discovery_finished") and self.on_discovery_finished:
                await self.on_discovery_finished()