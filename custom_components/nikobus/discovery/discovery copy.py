import asyncio
import logging

from .mapping import (
    DEVICE_TYPES,
    MODEL_TO_TYPE,
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
from .fileio import update_module_data, update_button_data
from ..nkbprotocol import make_pc_link_inventory_command

_LOGGER = logging.getLogger(__name__)

class NikobusDiscovery:
    def __init__(self, hass, coordinator):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._hass = hass
        self._timeout_seconds = 10.0
        self._discovery_response_timer = None
        self._discovery_running = False
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

    def _cancel_timeout(self):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

    def _cancel_discovery_timer(self):
        if self._discovery_response_timer:
            self._discovery_response_timer.cancel()
            self._discovery_response_timer = None

    async def _timeout_waiter(self):
        try:
            await asyncio.sleep(self._timeout_seconds)
            if not self._message_complete:
                _LOGGER.debug("Timeout reached. Processing complete message.")
                await self.process_complete_message()
        except asyncio.CancelledError:
            pass

    async def _start_discovery_finish_timer(self, delay=2.0):
        self._cancel_discovery_timer()
        self._discovery_response_timer = asyncio.create_task(self._finish_discovery_after_delay(delay))

    async def _finish_discovery_after_delay(self, delay):
        await asyncio.sleep(delay)
        _LOGGER.info("Nikobus inventory: discovery finished after last response.")
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._payload_buffer = ""
        self._chunks = []
        if hasattr(self._coordinator.nikobus_command, "clear_command_queue"):
            await self._coordinator.nikobus_command.clear_command_queue()
        if hasattr(self, "on_discovery_finished") and self.on_discovery_finished:
            await self.on_discovery_finished()
        self._discovery_running = False

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
            if hasattr(self, "on_discovery_finished") and self.on_discovery_finished:
                await self.on_discovery_finished()
            return

        base_command = f"10{device_address}"
        self._module_address = device_address
        self._discovery_running = True

        # Discovery range logic
        if self._coordinator.discovery_module:
            base_command = f"10{device_address[2:4] + device_address[:2]}"
            self._module_type = self._coordinator.get_module_type(device_address)
            if self._module_type == "dimmer_module":
                base_command = f"22{device_address[2:4] + device_address[:2]}"
                command_range = range(0x20, 0x100)  # 0x20 to 0xFF inclusive
            else:
                command_range = range(0x10, 0x100)  # 0x10 to 0xFF inclusive
        else:
            command_range = range(0xA0, 0x100)      # 0xA0 to 0xFF inclusive

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
            if all(b == 0xFF for b in data_bytes):
                # All FF = empty slot, skip silently, but still restart timer
                await self._start_discovery_finish_timer()
                return

            device_type_hex = f"{payload_bytes[7]:02X}"
            slice_end = 13 if len(payload_bytes) > 13 else len(payload_bytes)
            converted_address = payload_bytes[11:slice_end][::-1].hex().upper()

            if (
                device_type_hex == "FF" and
                (converted_address == "FFFFFF" or all(b in (0x00, 0xFF) for b in data_bytes))
            ):
                await self._start_discovery_finish_timer()
                return

            device_info = classify_device_type(device_type_hex, DEVICE_TYPES)
            category = device_info.get("Category", "Unknown")
            name = device_info.get("Name", "Unknown")
            model = device_info.get("Model", "N/A")
            channels = device_info.get("Channels", 0)

            if category == "Unknown":
                _LOGGER.warning(
                    "Unknown device detected: Type %s at Address %s. "
                    "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                    device_type_hex,
                    converted_address,
                )
                await self._start_discovery_finish_timer()
                return

            device_type_key = MODEL_TO_TYPE.get(model, "other_module")
            base_device = {
                "description": name,
                "category": category,
                "model": model,
                "address": converted_address,
                "channels": channels,
            }
            if device_type_key not in self.discovered_devices:
                self.discovered_devices[device_type_key] = {}
            self.discovered_devices[device_type_key][converted_address] = base_device

            _LOGGER.info(
                "Discovered %s - %s, Model: %s, at Address: %s",
                category,
                name,
                model,
                converted_address,
            )

            if category == "Module":
                await update_module_data(self._hass.config.path(""), self.discovered_devices)
            elif category == "Button":
                await update_button_data(
                    self._hass.config.path(""),
                    self.discovered_devices,
                    KEY_MAPPING,
                    convert_nikobus_address,
                )

            await self._start_discovery_finish_timer()

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

            data_with_crc = message[len(matched_header):]
            data = data_with_crc[:-6]
            payload_data = data[4:]

            self._payload_buffer += payload_data
            self._cancel_timeout()
            self._timeout_task = asyncio.create_task(self._timeout_waiter())

            chunk_lengths = {
                "switch_module": 12,
                "dimmer_module": 16,
                "roller_module": 12,
            }
            expected_chunk_length = chunk_lengths.get(self._module_type)

            _LOGGER.debug(
                "Chunk length determined for module_type=%r: %r",
                self._module_type,
                expected_chunk_length
            )

            while expected_chunk_length and len(self._payload_buffer) >= expected_chunk_length:
                candidate_chunk = self._payload_buffer[:expected_chunk_length]
                if candidate_chunk.strip().upper() == "F" * expected_chunk_length:
                    _LOGGER.debug("Termination chunk encountered: %r", candidate_chunk)
                    self._payload_buffer = self._payload_buffer[expected_chunk_length:]
                    await self._coordinator.nikobus_command.clear_command_queue()
                    self._message_complete = True
                    self._cancel_timeout()
                    await self.process_complete_message()
                    return
                self._chunks.append(candidate_chunk)
                _LOGGER.debug("Extracted chunk: %r", candidate_chunk)
                self._payload_buffer = self._payload_buffer[expected_chunk_length:]
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
                if decoded is not None:
                    new_commands.append(decoded)
                    _LOGGER.debug("Decoded chunk: %r", reversed_chunk)
                else:
                    _LOGGER.error("Failed to decode chunk: %r", reversed_chunk)

            self._decoded_buffer = {
                "module_address": self._module_address,
                "commands": new_commands,
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

            self._payload_buffer = ""
            self._decoded_buffer = {"module_address": None, "commands": []}
            self._module_address = None
            self._message_complete = False

        except Exception as e:
            _LOGGER.error("Error during process_complete_message: %s", e)
        finally:
            self.reset_state()
