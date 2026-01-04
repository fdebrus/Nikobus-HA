import asyncio
import logging
from datetime import datetime, timezone

from .base import DecodedCommand
from .dimmer_decoder import DimmerDecoder, EXPECTED_CHUNK_LEN
from .shutter_decoder import ShutterDecoder
from .switch_decoder import SwitchDecoder
from .mapping import (
    CHANNEL_MAPPING,
    DEVICE_TYPES,
    KEY_MAPPING,
    KEY_MAPPING_MODULE,
    get_module_type_from_device_type,
)
from .protocol import classify_device_type, convert_nikobus_address, reverse_hex
from ..const import DEVICE_INVENTORY
from .fileio import merge_discovered_links, update_button_data, update_module_data
from ..nkbprotocol import make_pc_link_inventory_command

_LOGGER = logging.getLogger(__name__)

DIMMER_EMPTY_RESPONSE_THRESHOLD = 8


def add_to_command_mapping(command_mapping, decoded_command, module_address):
    """Store decoded command information, allowing one-to-many button mappings."""
    push_button_address = decoded_command.get("push_button_address")
    key_raw = decoded_command.get("key_raw")
    if push_button_address is None or key_raw is None:
        return

    mapping_key = (push_button_address, key_raw)
    outputs = command_mapping.setdefault(mapping_key, [])
    channel_number = decoded_command.get("channel")

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


async def _notify_discovery_finished(discovery) -> None:
    """Call the discovery finished callback when available."""

    callback = getattr(discovery, "on_discovery_finished", None)
    if callback:
        await callback()


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
        self._module_address = None
        self._module_type = None
        self._module_channels: int | None = None
        self._coordinator.discovery_running = False
        self._coordinator.discovery_module = False
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

    async def query_module_inventory(self, device_address):
        if device_address == "ALL":
            all_addresses = self._coordinator.get_all_module_addresses()
            for addr in all_addresses:
                _LOGGER.info("Discovery started | module=%s", addr)
                self._coordinator.discovery_running = True
                self._coordinator.discovery_module = True
                self._coordinator.discovery_module_address = addr
                await self.query_module_inventory(addr)
                while self._coordinator.discovery_module:
                    await asyncio.sleep(0.5)
                _LOGGER.info("Discovery finished | module=%s", addr)
            self.reset_state()
            await _notify_discovery_finished(self)
            return

        base_command = f"10{device_address}"
        self._module_address = device_address

        if self._coordinator.discovery_module:
            base_command = f"10{device_address[2:4] + device_address[:2]}"
            self._module_type = self._coordinator.get_module_type(device_address)
            if self._module_type == "dimmer_module":
                base_command = f"22{device_address[2:4] + device_address[:2]}"
                command_range = range(0x10, 0x100)
            else:
                command_range = range(0x10, 0x100)
        else:
            command_range = range(0xA4, 0x100)

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
                _LOGGER.debug(
                    "Discovery skipped | type=inventory module=%s reason=empty_register",
                    self._module_address,
                )
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
                module_type = get_module_type_from_device_type(device_type_hex)
                last_seen = datetime.now(timezone.utc).isoformat()
                base_device = {
                    "description": name,
                    "discovered_name": name,
                    "category": category,
                    "device_type": device_type_hex,
                    "model": model,
                    "address": converted_address,
                    "channels": channels,
                    "channels_count": channels,
                    "module_type": module_type,
                    "discovered": True,
                    "last_seen": last_seen,
                }
                self.discovered_devices[converted_address] = base_device
            else:
                existing = self.discovered_devices[converted_address]
                existing.update(
                    {
                        "discovered_name": name,
                        "category": category,
                        "device_type": device_type_hex,
                        "model": model,
                        "channels": channels,
                        "channels_count": channels,
                        "module_type": get_module_type_from_device_type(device_type_hex),
                        "discovered": True,
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    }
                )

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

            coordinator_channels = self._coordinator.get_module_channel_count(address)
            discovered_channels = self.discovered_devices.get(address, {}).get("channels")
            self._module_channels = coordinator_channels or discovered_channels

            decoder = self._get_decoder()
            if decoder is None:
                _LOGGER.error("No decoder available for module type: %s", self._module_type)
                return

            if hasattr(decoder, "set_module_address"):
                decoder.set_module_address(address)

            if decoder.module_type == "dimmer_module":
                commands = decoder.decode(message)
                if commands:
                    self._module_address = address
                    await self._handle_decoded_commands(address, commands)
                return

            analysis = decoder.analyze_frame_payload(self._payload_buffer, payload_and_crc)
            if analysis is None:
                _LOGGER.error("Unable to analyze frame payload for message: %s", message)
                return

            self._module_address = address
            self._payload_buffer = analysis["remainder"]

            decoded_commands: list[DecodedCommand] = []
            for chunk in analysis["chunks"]:
                if chunk.strip().upper() == "F" * len(chunk):
                    _LOGGER.debug(
                        "Discovery skipped | type=%s module=%s reason=terminator payload=%s",
                        decoder.module_type,
                        address,
                        chunk,
                    )
                    continue
                decoded_commands.extend(decoder.decode(chunk, module_address=address))

            if decoded_commands:
                await self._handle_decoded_commands(address, decoded_commands)

        except Exception as e:
            _LOGGER.error("Failed to parse module inventory response: %s", e)
            self.reset_state()
        finally:
            if self._coordinator.discovery_module:
                self.reset_state()
                await _notify_discovery_finished(self)

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

        _LOGGER.info(
            "Discovery decoded commands | module=%s count=%d",
            self._decoded_buffer["module_address"],
            len(self._decoded_buffer["commands"]),
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