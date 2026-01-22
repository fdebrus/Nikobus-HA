import asyncio
import logging

from .base import DecodedCommand, InventoryQueryType, InventoryResult
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
from ..const import DEVICE_ADDRESS_INVENTORY, DEVICE_INVENTORY
from .fileio import merge_discovered_links, update_button_data, update_module_data
from ..nkbprotocol import make_pc_link_inventory_command
from homeassistant.util import dt as dt_util

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
        self._module_timeout_seconds = 5.0
        self._inventory_timeout_seconds = 2.0
        self._decoders = [
            DimmerDecoder(coordinator),
            SwitchDecoder(coordinator),
            ShutterDecoder(coordinator),
        ]
        self._timeout_task: asyncio.Task | None = None
        self._inventory_timeout_task: asyncio.Task | None = None
        self.discovery_stage: str | None = None
        self._register_scan_queue: list[str] = []
        self._inventory_addresses: set[str] = set()
        self.reset_state()

    def reset_state(self, *, update_flags: bool = True):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None
        if self._inventory_timeout_task:
            self._inventory_timeout_task.cancel()
            self._inventory_timeout_task = None
        self._payload_buffer = ""
        self._module_address = None
        self._module_type = None
        self._module_channels: int | None = None
        self._register_scan_queue = []
        self._inventory_addresses = set()
        self._inventory_identity_queued: set[str] = set()
        self.discovery_stage = None
        if update_flags:
            self._coordinator.discovery_running = False
            self._coordinator.discovery_module = False
            self._coordinator.discovery_module_address = None
            self._coordinator.inventory_query_type = None

    def normalize_module_address(
        self, address: str, *, source: str, reverse_bus_order: bool = False
    ) -> str:
        """Return a canonical module address, logging when normalization occurs."""

        raw = (address or "").strip().upper()
        normalized = raw

        try:
            if reverse_bus_order:
                normalized = reverse_hex(raw)
        except ValueError:
            normalized = raw

        if normalized != raw:
            _LOGGER.debug(
                "Normalized module address | raw=%s normalized=%s source=%s",
                raw,
                normalized,
                source,
            )

        return normalized

    def _get_decoder(self):
        for decoder in getattr(self, "_decoders", []):
            if decoder.can_handle(self._module_type):
                return decoder
        return None

    def _is_known_module_address(self, address: str | None) -> bool:
        normalized = (address or "").upper()
        return any(
            normalized in modules for modules in self._coordinator.dict_module_data.values()
        )

    def _cancel_timeout(self) -> None:
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

    def _cancel_inventory_timeout(self) -> None:
        if self._inventory_timeout_task:
            self._inventory_timeout_task.cancel()
            self._inventory_timeout_task = None

    def _schedule_timeout(self) -> None:
        self._cancel_timeout()
        module_address = self._module_address
        self._timeout_task = asyncio.create_task(
            self._timeout_after(module_address)
        )

    def _schedule_inventory_timeout(self) -> None:
        self._cancel_inventory_timeout()
        self._inventory_timeout_task = asyncio.create_task(
            self._inventory_timeout_after()
        )

    async def _timeout_after(self, module_address: str | None) -> None:
        try:
            await asyncio.sleep(self._module_timeout_seconds)
        except asyncio.CancelledError:
            return
        await self._finalize_discovery(module_address)

    async def _inventory_timeout_after(self) -> None:
        try:
            await asyncio.sleep(self._inventory_timeout_seconds)
        except asyncio.CancelledError:
            return
        await self._finalize_inventory_phase()

    def _reset_module_context(self) -> None:
        self._payload_buffer = ""
        self._module_address = None
        self._module_type = None
        self._module_channels = None

    async def _finalize_discovery(self, module_address: str | None = None) -> None:
        self._cancel_timeout()
        resolved_address = (
            module_address
            or self._module_address
            or self._coordinator.discovery_module_address
        )
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._reset_module_context()

        if self.discovery_stage == "register_scan" and self._register_scan_queue:
            await self._start_next_register_scan()
            return

        await self._complete_discovery_run(resolved_address)

    async def _finalize_inventory_phase(self) -> None:
        self._cancel_inventory_timeout()
        if self.discovery_stage == "inventory_addresses" and self._inventory_addresses:
            pending_addresses = (
                self._inventory_addresses - self._inventory_identity_queued
            )
            if pending_addresses:
                await self._run_inventory_identity_queries(pending_addresses)
                self._inventory_identity_queued.update(pending_addresses)
            self.discovery_stage = "inventory_identity"
            self._schedule_inventory_timeout()
            return

        await update_module_data(self._hass, self.discovered_devices)
        await update_button_data(
            self._hass,
            self.discovered_devices,
            KEY_MAPPING,
            convert_nikobus_address,
        )

        output_modules = sorted(
            address
            for address, device in self.discovered_devices.items()
            if device.get("category") == "Module"
            and device.get("module_type")
            in {"switch_module", "dimmer_module", "roller_module"}
        )

        self.discovery_stage = "register_scan"
        self._register_scan_queue = output_modules
        _LOGGER.info(
            "PC Link inventory scan finished | discovered=%d",
            len(self.discovered_devices),
        )
        if output_modules:
            await self._start_next_register_scan()
        else:
            await self._complete_discovery_run(None)

    async def _run_inventory_identity_queries(self, addresses: set[str]) -> None:
        for address in sorted(addresses):
            bus_order_address = address[2:4] + address[:2]
            for base_command, command_code in (("10", "2E"), ("22", "1E")):
                payload = f"{base_command}{bus_order_address}{command_code}04"
                pc_link_command = make_pc_link_inventory_command(payload)
                await self._coordinator.nikobus_command.queue_command(pc_link_command)

    async def _start_next_register_scan(self) -> None:
        if not self._register_scan_queue:
            await self._complete_discovery_run(None)
            return

        next_module = self._register_scan_queue.pop(0)
        normalized_address = self.normalize_module_address(
            next_module, source="register_scan_queue"
        )
        _LOGGER.info("Discovery started | module=%s", normalized_address)
        self._coordinator.discovery_module = True
        self._coordinator.discovery_module_address = normalized_address
        await self.query_module_inventory(normalized_address, from_queue=True)

    async def _complete_discovery_run(self, resolved_address: str | None) -> None:
        self._cancel_inventory_timeout()
        _LOGGER.info("Discovery finished")
        self.reset_state()
        await _notify_discovery_finished(self)

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

    async def start_inventory_discovery(self):
        self.reset_state(update_flags=False)
        self.discovered_devices = {}
        self.discovery_stage = "inventory_addresses"
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._coordinator.discovery_running = True
        self._coordinator.inventory_query_type = InventoryQueryType.PC_LINK
        _LOGGER.info("PC Link inventory scan started")
        _LOGGER.debug("Queueing PC Link inventory command #A")
        await self._coordinator.nikobus_command.queue_command("#A")
        self._schedule_inventory_timeout()

    def handle_device_address_inventory(self, message: str) -> None:
        clean_message = message.strip("\x02\x03\r\n")
        marker_index = clean_message.find(DEVICE_ADDRESS_INVENTORY)
        if marker_index == -1:
            _LOGGER.debug(
                "Inventory record ignored | reason=missing_marker message=%s",
                message,
            )
            return
        start_index = marker_index + len(DEVICE_ADDRESS_INVENTORY)
        raw_address = (clean_message[start_index : start_index + 4] or "").upper()
        normalized = self.normalize_module_address(
            raw_address, source="device_address_inventory", reverse_bus_order=True
        )
        registry_start = start_index + 4
        registry_end = registry_start + 6
        registry_raw = ""
        if len(clean_message) >= registry_end:
            registry_raw = (clean_message[registry_start:registry_end] or "").upper()

        is_new = normalized not in self._inventory_addresses
        self._inventory_addresses.add(normalized)
        _LOGGER.debug(
            "Inventory record | raw=%s normalized=%s", raw_address, normalized
        )
        _LOGGER.info("Inventory record | address=%s", normalized)
        self._ensure_pc_link_address(normalized, source="device_address_inventory")
        if is_new and self.discovery_stage == "inventory_addresses":
            asyncio.create_task(
                self._queue_inventory_identity_queries_for_address(normalized)
            )
        self._schedule_inventory_timeout()

    async def _queue_inventory_identity_queries_for_address(self, address: str) -> None:
        if address in self._inventory_identity_queued:
            return
        await self._run_inventory_identity_queries({address})
        self._inventory_identity_queued.add(address)

    def _ensure_pc_link_address(self, address: str, *, source: str) -> None:
        if not address:
            return

        existing = self.discovered_devices.get(address)
        if existing and existing.get("module_type") != "pc_link":
            _LOGGER.debug(
                "Skipping PC Link address record | address=%s reason=existing_module_type",
                address,
            )
            return

        coordinator_modules = getattr(self._coordinator, "dict_module_data", {}) or {}
        known_pc_links = coordinator_modules.get("pc_link") or {}
        if known_pc_links and address not in known_pc_links:
            _LOGGER.debug(
                "Skipping PC Link address record | address=%s reason=known_pc_link_present source=%s",
                address,
                source,
            )
            return

        pc_link_info = DEVICE_TYPES.get("0A", {})
        name = pc_link_info.get("Name", "PC Link")
        model = pc_link_info.get("Model", "05-200")
        last_seen = dt_util.now().isoformat()
        module_type = get_module_type_from_device_type("0A")
        base_device = {
            "description": name,
            "discovered_name": name,
            "category": "Module",
            "device_type": "0A",
            "model": model,
            "address": address,
            "channels": 0,
            "channels_count": 0,
            "module_type": module_type,
            "discovered": True,
            "last_discovered": last_seen,
        }
        if existing:
            existing.update(base_device)
        else:
            self.discovered_devices[address] = base_device

        _LOGGER.info(
            "PC Link address recorded | address=%s source=%s",
            address,
            source,
        )

    async def query_module_inventory(self, device_address, *, from_queue: bool = False):
        if device_address == "ALL":
            all_addresses = self._coordinator.get_all_module_addresses()
            for addr in all_addresses:
                _LOGGER.info("Discovery started | module=%s", addr)
                self._coordinator.discovery_running = True
                self._coordinator.discovery_module = True
                self._coordinator.discovery_module_address = addr
                await self.query_module_inventory(addr, from_queue=True)
                while self._coordinator.discovery_module:
                    await asyncio.sleep(0.5)
            self.reset_state()
            return

        normalized_address = self.normalize_module_address(
            device_address, source="query_module_inventory"
        )

        self.discovery_stage = self.discovery_stage or "register_scan"
        base_command = f"10{normalized_address}"
        self._module_address = normalized_address
        self._coordinator.inventory_query_type = InventoryQueryType.MODULE

        discovered_device = self.discovered_devices.get(normalized_address, {})

        if not self._coordinator.discovery_module:
            _LOGGER.info("Discovery started | module=%s", normalized_address)
            if not from_queue:
                self._coordinator.discovery_running = True
            self._coordinator.discovery_module = True
            self._coordinator.discovery_module_address = normalized_address

        if self._module_type is None:
            self._module_type = discovered_device.get("module_type") or self._coordinator.get_module_type(
                normalized_address
            )

        non_output_modules = {"pc_link", "pc_logic", "feedback_module", "other_module"}
        is_output_module = self._module_type not in non_output_modules

        coordinator_channels = (
            self._coordinator.get_module_channel_count(normalized_address)
            if self._is_known_module_address(normalized_address)
            else 0
        )
        discovered_channels = discovered_device.get("channels")
        self._module_channels = next(
            (count for count in (coordinator_channels, discovered_channels) if count),
            None,
        )

        if self._coordinator.discovery_module:
            base_command = f"10{normalized_address[2:4] + normalized_address[:2]}"
            if self._module_type == "dimmer_module":
                base_command = f"22{normalized_address[2:4] + normalized_address[:2]}"
                command_range = range(0x10, 0x100)
            else:
                command_range = range(0x10, 0x100)
        else:
            command_range = range(0xA4, 0x100)

        if not is_output_module:
            _LOGGER.info(
                "Skipping register scan for non-output module | module=%s type=%s",
                normalized_address,
                self._module_type,
            )
            if self.discovery_stage == "inventory":
                return

            await self._finalize_discovery(normalized_address)
            return

        for cmd in command_range:
            partial_hex = f"{base_command}{cmd:02X}04"
            pc_link_command = make_pc_link_inventory_command(partial_hex)
            await self._coordinator.nikobus_command.queue_command(pc_link_command)

    async def parse_inventory_response(self, payload) -> InventoryResult | None:
        result = InventoryResult()
        try:
            self.discovery_stage = self.discovery_stage or "inventory"
            if payload.startswith("$") and "$" in payload[1:]:
                payload = payload.split("$")[-1]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)
            data_bytes = payload_bytes[2:18] if len(payload_bytes) >= 18 else payload_bytes[2:]

            device_type_hex = f"{payload_bytes[7]:02X}"
            self._schedule_inventory_timeout()

            if device_type_hex == "FF":
                _LOGGER.debug(
                    "Discovery skipped | type=inventory module=%s reason=empty_register",
                    self._module_address,
                )
                return result

            device_info = classify_device_type(device_type_hex, DEVICE_TYPES)
            category = device_info.get("Category") or "Module"
            name = device_info.get("Name") or "Unknown"
            model = device_info.get("Model") or "N/A"
            channels = device_info.get("Channels", 0) or 0
            slice_end = 13 if category == "Module" else 14
            raw_address = payload_bytes[11:slice_end].hex().upper()
            converted_address = self.normalize_module_address(
                raw_address,
                source="device_address_inventory",
                reverse_bus_order=True,
            )

            if device_info.get("Category", "Unknown") == "Unknown":
                _LOGGER.warning(
                    "Unknown device detected: Type %s at Address %s. "
                    "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                    device_type_hex,
                    converted_address,
                )

            module_type = get_module_type_from_device_type(device_type_hex)

            last_seen = dt_util.now().isoformat()
            device_entry = {
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
                "last_discovered": last_seen,
            }

            if category == "Button":
                result.buttons.append(device_entry)
            else:
                result.modules.append(device_entry)

            self._coordinator.apply_inventory_update(result, self.discovered_devices)

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
            return result
        except Exception as e:
            _LOGGER.error("Failed to parse Nikobus payload: %s", e)
            self.reset_state()
            return None

    async def parse_module_inventory_response(self, message):
        try:
            matched_header = next(
                (h for h in DEVICE_INVENTORY if message.startswith(h)), None
            )
            if not matched_header:
                return

            frame_body = message[len(matched_header) :]

            if len(frame_body) < 4:
                return

            address_segment = frame_body[:4].upper()
            address = reverse_hex(address_segment)
            payload_and_crc = frame_body[4:]

            self._module_address = address

            if self._module_type is None:
                discovered = self.discovered_devices.get(address, {})
                self._module_type = discovered.get("module_type") or self._coordinator.get_module_type(
                    address
                )

            coordinator_channels = (
                self._coordinator.get_module_channel_count(address)
                if self._is_known_module_address(address)
                else 0
            )
            discovered_channels = self.discovered_devices.get(address, {}).get("channels")
            self._module_channels = next(
                (count for count in (coordinator_channels, discovered_channels) if count),
                None,
            )

            decoder = self._get_decoder()
            if decoder is None:
                _LOGGER.error("No decoder available for module type: %s", self._module_type)
                return

            if hasattr(decoder, "set_module_address"):
                decoder.set_module_address(address)
            if hasattr(decoder, "set_module_channel_count"):
                decoder.set_module_channel_count(self._module_channels)

            if decoder.module_type == "dimmer_module":
                commands = decoder.decode(message)
                if commands:
                    self._module_address = address
                    await self._handle_decoded_commands(address, commands)
                self._schedule_timeout()
                return

            analysis = decoder.analyze_frame_payload(self._payload_buffer, payload_and_crc)
            if analysis is None:
                self._schedule_timeout()
                return

            self._module_address = address
            self._payload_buffer = analysis["remainder"]

            decoded_commands: list[DecodedCommand] = []
            terminator_seen = False
            for chunk in analysis["chunks"]:
                normalized_chunk = chunk.strip().upper()
                if not normalized_chunk:
                    continue
                if normalized_chunk == "F" * len(normalized_chunk):
                    terminator_seen = True
                    continue
                decoded_commands.extend(
                    decoder.decode(normalized_chunk, module_address=address)
                )

            if decoded_commands:
                await self._handle_decoded_commands(address, decoded_commands)

            completion_detected = False
            if terminator_seen and not self._payload_buffer:
                completion_detected = True

            if not self._coordinator.discovery_module:
                completion_detected = True

            if completion_detected:
                await self._finalize_discovery(address)
            else:
                self._schedule_timeout()

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
