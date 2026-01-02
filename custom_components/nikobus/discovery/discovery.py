import asyncio
import logging

from .base import DecodedCommand
from .dimmer_decoder import DimmerDecoder
from .shutter_decoder import ShutterDecoder
from .switch_decoder import SwitchDecoder
from .mapping import DEVICE_TYPES, KEY_MAPPING, KEY_MAPPING_MODULE, CHANNEL_MAPPING
from .protocol import classify_device_type, convert_nikobus_address, reverse_hex
from ..const import DEVICE_INVENTORY
from .fileio import merge_discovered_links, update_button_data, update_module_data
from ..nkbprotocol import make_pc_link_inventory_command

_LOGGER = logging.getLogger(__name__)


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
        self._decoders = [
            DimmerDecoder(coordinator),
            SwitchDecoder(coordinator),
            ShutterDecoder(coordinator),
        ]
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

    def _get_decoder(self):
        for decoder in getattr(self, "_decoders", []):
            if decoder.can_handle(self._module_type):
                return decoder
        return None

    def _analyze_frame_payload(self, address, payload_and_crc):
        decoder = self._get_decoder()
        if decoder is None:
            return None

        if decoder.module_type == "dimmer_module":
            payload_and_crc = payload_and_crc.upper()
            if len(payload_and_crc) < 16:
                _LOGGER.error(
                    "Dimmer payload too short for alignment | address=%s payload=%s",
                    address,
                    payload_and_crc,
                )
                return None
            return {
                "crc_len": max(len(payload_and_crc) - 16, 0),
                "crc": payload_and_crc[16:],
                "payload_region": payload_and_crc[:16],
                "chunks": [payload_and_crc[:16]],
                "meta": [],
                "remainder": "",
                "score": 0,
            }

        return decoder.analyze_frame_payload(self._payload_buffer, payload_and_crc)

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

            _LOGGER.debug(
                "Inventory classification | module_address=%s device_type=%s model=%s channels=%s",
                converted_address,
                device_type_hex,
                model,
                channels,
            )

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

            frame_body = message[len(matched_header) :]

            if len(frame_body) < 4:
                _LOGGER.error("Frame body too short to contain address and payload.")
                return

            address_segment = frame_body[:4].upper()
            address = reverse_hex(address_segment)
            payload_and_crc = frame_body[4:]

            if self._module_type is None:
                self._module_type = self._coordinator.get_module_type(address)

            decoder = self._get_decoder()
            if decoder is None:
                _LOGGER.error("No decoder available for module type: %s", self._module_type)
                return

            if decoder.module_type == "dimmer_module":
                commands = decoder.decode(message)
                if commands:
                    self._module_address = address
                    await self._handle_decoded_commands(address, commands)
                    self._message_complete = True
                    self.reset_state()
                    if hasattr(self, "on_discovery_finished") and self.on_discovery_finished:
                        await self.on_discovery_finished()
                else:
                    _LOGGER.debug("Dimmer decoder returned no commands for message: %s", message)
                return

            analysis = decoder.analyze_frame_payload(self._payload_buffer, payload_and_crc)
            if analysis is None:
                _LOGGER.error("Unable to analyze frame payload for message: %s", message)
                return

            self._module_address = address
            self._payload_buffer = analysis["remainder"]
            self._chunks.extend(analysis["chunks"])

            self._cancel_timeout()
            self._timeout_task = asyncio.create_task(self._timeout_waiter())

            if analysis.get("terminated"):
                _LOGGER.debug("Termination chunk encountered for address %s", address)
                await self._coordinator.nikobus_command.clear_command_queue()
                self._message_complete = True
                self._cancel_timeout()
                await self.process_complete_message()
                return

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

    async def _handle_decoded_commands(
        self, module_address: str | None, decoded_commands: list[DecodedCommand]
    ):
        new_commands = []
        command_mapping = {}
        for command in decoded_commands:
            if not isinstance(command, DecodedCommand):
                continue
            decoded = command.metadata or {}
            if decoded.get("push_button_address") is None:
                continue

            new_commands.append(decoded)
            if module_address:
                add_to_command_mapping(command_mapping, decoded, module_address)

        self._decoded_buffer = {
            "module_address": module_address,
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

            decoder = self._get_decoder()
            if decoder is None:
                _LOGGER.error("No decoder found for module type %s", self._module_type)
                return

            _LOGGER.debug(
                "Processing complete message with %d chunks.", len(chunks_to_process)
            )
            decoded_commands: list[DecodedCommand] = []
            for chunk in chunks_to_process:
                _LOGGER.debug("Decoding chunk: %r", chunk)
                decoded_commands.extend(decoder.decode(chunk))

            await self._handle_decoded_commands(self._module_address, decoded_commands)

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


def run_decoder_harness(coordinator):
    """Lightweight harness to exercise discovery decoders without full HA runtime."""

    sample_messages = [
        "$0522$1E6C0E5F1550000300B4FF452CA9",  # dimmer frame with expected 16-hex chunk
        "5F1550000300B4FF",  # raw chunk form
    ]

    decoders = [DimmerDecoder(coordinator), SwitchDecoder(coordinator), ShutterDecoder(coordinator)]
    for message in sample_messages:
        _LOGGER.info("HARNESS message=%s", message)
        for decoder in decoders:
            results = decoder.decode(message)
            if not results:
                continue
            for result in results:
                _LOGGER.info(
                    "HARNESS decoder=%s payload_len=%s chunk_len=%s payload=%s metadata=%s",
                    decoder.module_type,
                    len(result.payload_hex) if result.payload_hex else "?",
                    len(result.chunk_hex) if result.chunk_hex else "?",
                    result.payload_hex,
                    result.metadata,
                )