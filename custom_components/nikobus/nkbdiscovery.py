import asyncio
import logging
import os
import json
import aiofiles

from homeassistant.helpers.device_registry import async_get

from .nkbprotocol import make_pc_link_inventory_command
from .const import (
    DOMAIN,
    DEVICE_INVENTORY,
    DEVICE_TYPES,
    SWITCH_MODE_MAPPING,
    ROLLER_MODE_MAPPING,
    DIMMER_MODE_MAPPING,
    SWITCH_TIMER_MAPPING,
    ROLLER_TIMER_MAPPING,
    DIMMER_TIMER_MAPPING,
    KEY_MAPPING,
    KEY_MAPPING_MODULE,
    CHANNEL_MAPPING,
)

_LOGGER = logging.getLogger(__name__)

class NikobusDiscovery:
    def __init__(self, hass, coordinator):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._hass = hass
        self._payload_buffer = ""
        self._chunks = []
        self._module_address = None
        self._module_type = None
        self._message_complete = False
        self._timeout_task = None
        self._timeout_seconds = 5.0  # Adjust the timeout duration

    def _cancel_timeout(self):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

    async def _timeout_waiter(self):
        try:
            await asyncio.sleep(self._timeout_seconds)
            # If timeout expires and message is not already marked complete, process what we have.
            if not self._message_complete:
                _LOGGER.debug("Timeout reached. Processing complete message.")
                await self.process_complete_message()
        except asyncio.CancelledError:
            # Task cancelled because new data arrived in time.
            pass

    def _reverse_hex(self, hex_str):
        b = bytes.fromhex(hex_str)
        reversed_b = b[::-1]
        return reversed_b.hex().upper()

    def _classify_device_type(self, device_type_hex):
        return DEVICE_TYPES.get(
            device_type_hex,
            {
                "Category": "Unknown",
                "Model": "Unknown",
                "Channels": 0,
                "Name": "Unknown",
            },
        )

    def _convert_nikobus_address(self, address_string: str) -> dict[str, any]:
        try:
            # Convert input hexadecimal string to an integer.
            address = int(address_string, 16)
            nikobus_address = 0
        
            # Reverse the order of the lowest 21 bits.
            for i in range(21):
                nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)
            nikobus_address <<= 1  # Final left shift appends a zero bit.
        
            # Calculate the button value from bits 21-23.
            button = (address >> 21) & 0x07
        
            # Add the button value to the Nikobus address.
            final_address = nikobus_address + button
        
            # Return the final Nikobus address as a 6-digit hex string.
            return {"nikobus_address": f"{final_address:06X}"}
        except ValueError:
            return f"[{address_string}]"

    async def query_module_inventory(self, device_address):
        if device_address == "ALL":
            all_addresses = self._coordinator.get_all_module_addresses()
            for addr in all_addresses:
                _LOGGER.info("Starting discovery for module: %s", addr)
                self._coordinator.discovery_running = True
                self._coordinator.discovery_module = True
                await self.query_module_inventory(addr)
                while self._coordinator.discovery_module:
                    await asyncio.sleep(0.5)
                _LOGGER.info("Completed discovery for module: %s", addr)
            return

        base_command = f"10{device_address}"
        self._module_address = device_address
        if self._coordinator.discovery_module:
            base_command = f"10{device_address[2:4] + device_address[:2]}"
            self._module_type = self._coordinator.get_module_type(device_address)
            if self._module_type == "dimmer_module":
                base_command = f"22{device_address[2:4] + device_address[:2]}"
                command_range = range(0x20, 0xFF)
            else:
                command_range = range(0x10, 0xFF)
        else:
            command_range = range(0xA4, 0xFF)
        
        for cmd in command_range:
            partial_hex = f"{base_command}{cmd:02X}04"
            pc_link_command = make_pc_link_inventory_command(partial_hex)
            await self._coordinator.nikobus_command.queue_command(pc_link_command)

    async def process_mode_button_press(self, message):
        stripped_message = message.lstrip("$")
        payload_bytes = bytes.fromhex(stripped_message)
        device_type_hex = f"{payload_bytes[5]:02X}"
        device_info = self._classify_device_type(device_type_hex)
        converted_address = payload_bytes[1:3][::-1].hex().upper()
        if converted_address in self.discovered_devices:
            await self.update_module_data()
            return
        num_channels = int(device_info.get("Channels", 0))
        channels = [
            {"description": f"{device_info['Name']} Output {i + 1}"}
            for i in range(num_channels)
        ]
        self.discovered_devices[converted_address] = {
            "category": device_info["Category"],
            "description": device_info["Name"],
            "model": device_info["Model"],
            "address": converted_address,
            "channels": channels,
        }
        _LOGGER.info(
            "Discovered device: %s %s at %s from message: %s",
            device_type_hex,
            device_info,
            converted_address,
            stripped_message,
        )
        await self.update_module_data()

    async def parse_inventory_response(self, payload):
        try:
            if payload.startswith("$0510$"):
                payload = payload[6:]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)
            device_type_hex = f"{payload_bytes[7]:02X}"
            device_info = self._classify_device_type(device_type_hex)
            category = device_info.get("Category", "Unknown")
            name = device_info.get("Name", "Unknown")
            model = device_info.get("Model", "N/A")
            channels = device_info.get("Channels", 0)
            slice_end = 13 if category == "Module" else 14
            converted_address = payload_bytes[11:slice_end][::-1].hex().upper()
            if "FFFFFF" in converted_address or "FF" in device_type_hex:
                await self._coordinator.nikobus_command.clear_command_queue()
                return
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
                if category == "Module":
                    num_channels = int(channels)
                    base_device["channels"] = [
                        {"description": f"{name} Output {i + 1}"}
                        for i in range(num_channels)
                    ]
                self.discovered_devices[converted_address] = base_device
            _LOGGER.info(
                "Discovered %s - %s, Model: %s, at Address: %s",
                category,
                name,
                model,
                converted_address,
            )
            if category == "Module":
                await self.update_module_data()
            elif category == "Button":
                await self.update_button_data()
        except Exception as e:
            _LOGGER.error("Failed to parse Nikobus payload: %s", e)

    async def update_module_data(self) -> None:
        module_data = {
            "switch_module": {},
            "dimmer_module": {},
            "roller_module": {},
            "other_module": {},
        }
        for device in self.discovered_devices.values():
            if device.get("category") == "Button":
                continue
            address = device.get("address")
            description = device.get("description", "")
            if "Switch Module" in description or "Compact Switch Module" in description:
                module_data["switch_module"][address] = device
            elif "Dimmer Module" in description:
                module_data["dimmer_module"][address] = device
            elif "Roller Shutter Module" in description:
                for channel in device.get("channels", []):
                    channel["operation_time"] = "40"
                module_data["roller_module"][address] = device
            else:
                module_data["other_module"][address] = device
        try:
            file_path = self._hass.config.path("nikobus_module_discovered.json")
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(module_data, indent=4))
            _LOGGER.info("Module data written to file: %s", file_path)
        except Exception as e:
            _LOGGER.error("Failed to write module data to file: %s", e)

    async def update_button_data(self):
        file_path = self._hass.config.path("nikobus_button_config.json")
        if os.path.exists(file_path):
            async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
                try:
                    existing_json = json.loads(await file.read())
                    existing_data = existing_json.get("nikobus_button", [])
                    if not isinstance(existing_data, list):
                        existing_data = []
                except json.JSONDecodeError:
                    existing_data = []
        else:
            existing_data = []
        updated_data = existing_data.copy()
        lookup = {button.get("address"): button for button in updated_data if "address" in button}
        for device_address, device in self.discovered_devices.items():
            if device.get("category") != "Button":
                continue
            description = device.get("description", "")
            model = device.get("model", "")
            num_channels = device.get("channels", 0)
            if num_channels == 1:
                keys = ["1A"]
            elif num_channels == 2:
                keys = ["1A", "1B"]
            elif num_channels == 4:
                keys = ["1A", "1B", "1C", "1D"]
            elif num_channels == 8:
                keys = ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"]
            else:
                _LOGGER.error(f"Unexpected number of channels: {num_channels} for device {device_address}")
                continue
            mapping = KEY_MAPPING.get(num_channels, {})
            channels_data = {}
            converted_result = self._convert_nikobus_address(device_address)
            converted_address = converted_result["nikobus_address"]
            original_nibble = int(converted_address[0], 16)
            for idx, key in enumerate(keys, start=1):
                if key in mapping:
                    add_value = int(mapping[key], 16)
                    new_nibble_value = original_nibble + add_value
                    new_nibble_hex = f"{new_nibble_value:X}"
                    updated_addr = new_nibble_hex + converted_address[1:]
                    channels_data[f"channel_{idx}"] = {"key": key, "address": updated_addr}
            device["channels_data"] = channels_data
            for channel_info in channels_data.values():
                discovered_channel_address = channel_info["address"]
                key = channel_info["key"]
                new_info = {
                    "type": description,
                    "model": model,
                    "address": device_address,
                    "channels": num_channels,
                    "key": key,
                }
                button = lookup.get(discovered_channel_address)
                if button:
                    discovered_list = button.setdefault("discovered_info", [])
                    found_info = next(
                        (info for info in discovered_list if info.get("key") == new_info["key"] and info.get("address") == new_info["address"]),
                        None
                    )
                    if found_info:
                        if (found_info.get("type") != new_info["type"] or
                            found_info.get("model") != new_info["model"] or
                            found_info.get("channels") != new_info["channels"]):
                            found_info.update({
                                "type": new_info["type"],
                                "model": new_info["model"],
                                "channels": new_info["channels"],
                            })
                    else:
                        discovered_list.append(new_info)
                else:
                    new_button = {
                        "description": f"{description} #N{discovered_channel_address}",
                        "address": discovered_channel_address,
                        "impacted_module": [{"address": "", "group": ""}],
                        "discovered_info": [new_info]
                    }
                    updated_data.append(new_button)
                    lookup[discovered_channel_address] = new_button
        try:
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                output_json = {"nikobus_button": updated_data}
                await file.write(json.dumps(output_json, indent=4))
            _LOGGER.info("Updated button data written to file: %s", file_path)
        except Exception as e:
            _LOGGER.error("Failed to write button data to file: %s", e)

    async def parse_module_inventory_response(self, message):
        _LOGGER.debug("Received message: %r", message)

        matched_header = next((h for h in DEVICE_INVENTORY if message.startswith(h)), None)
        if not matched_header:
            _LOGGER.error("Message does not start with expected header.")
            return

        # Remove header and CRC (last 6 hex characters)
        data_with_crc = message[len(matched_header):]

        data = data_with_crc[:-6]
        payload_data = data[4:]

        self._payload_buffer += payload_data

        # (Re)start the timeout every time new data is appended.
        self._cancel_timeout()
        self._timeout_task = asyncio.create_task(self._timeout_waiter())

        # Determine expected chunk length based on module_type.
        chunk_lengths = {
            "switch_module": 12,
            "dimmer_module": 16,
            "roller_module": 12,
        }
        
        expected_chunk_length = chunk_lengths.get(self._module_type)

        while len(self._payload_buffer) >= expected_chunk_length:
            candidate_chunk = self._payload_buffer[:expected_chunk_length]
    
            # Check if this is the termination chunk (all F's).
            if candidate_chunk.strip().upper() == "F" * expected_chunk_length:
                _LOGGER.debug("Termination chunk encountered: %r", candidate_chunk)
                # Remove the termination chunk and process the complete message.
                self._payload_buffer = self._payload_buffer[expected_chunk_length:]
                await self._coordinator.nikobus_command.clear_command_queue()
                self._message_complete = True
                self._cancel_timeout()
                await self.process_complete_message()
                return

            # If valid, accept the chunk and remove it from the buffer.
            self._chunks.append(candidate_chunk)
            _LOGGER.debug("Extracted chunk: %r", candidate_chunk)
            self._payload_buffer = self._payload_buffer[expected_chunk_length:]

    async def process_complete_message(self):
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

        _LOGGER.debug("Processing complete message with %d chunks.", len(chunks_to_process))
        new_commands = []
        for chunk in chunks_to_process:
            _LOGGER.debug("Decoding chunk: %r", chunk[:12])
            reversed_chunk = self._reverse_hex(chunk[:12])
            decoded = self.decode_command_payload(reversed_chunk)
            if decoded is not None:
                new_commands.append(decoded)
                _LOGGER.debug("Decoded chunk: %r", reversed_chunk)
            else:
                _LOGGER.error("Failed to decode chunk: %r", reversed_chunk)

        if not hasattr(self, "_decoded_buffer"):
            self._decoded_buffer = {"module_address": self._module_address, "commands": []}
        else:
            if self._decoded_buffer.get("module_address") is None:
                self._decoded_buffer["module_address"] = self._module_address

        self._decoded_buffer["commands"].extend(new_commands)

        _LOGGER.info("Decoded Button Commands:")
        _LOGGER.info("module_address: %s", self._decoded_buffer["module_address"])
        for idx, cmd in enumerate(self._decoded_buffer["commands"], start=1):
            _LOGGER.info(
                "Command %d: Payload: %s, Button Address: %s, Push Button Address: %s, Key: %s, Channel: %s, Timer: %s, Mode: %s",
                idx, cmd.get("payload"), cmd.get("button_address"), cmd.get("push_button_address"),
                cmd.get("K"), cmd.get("C"), cmd.get("T"), cmd.get("M")
            )

        grouped = {"module_address": self._decoded_buffer["module_address"], "channels": {}}
        for cmd in self._decoded_buffer["commands"]:
            channel = cmd.get("C")
            new_cmd = {
                "button_address": cmd.get("button_address"),
                "push_button_address": cmd.get("push_button_address"),
                "button_key": cmd.get("K"),
                "timer": cmd.get("T"),
                "mode": cmd.get("M"),
                "channel": cmd.get("C")
            }
            if channel not in grouped["channels"]:
                grouped["channels"][channel] = []
            grouped["channels"][channel].append(new_cmd)
        try:
            sorted_channels = dict(
                sorted(
                    grouped["channels"].items(),
                    key=lambda x: int(x[0].split()[1])
                    if len(x[0].split()) > 1 and x[0].split()[1].isdigit() else 0
                )
            )
            grouped["channels"] = sorted_channels
        except Exception as e:
            _LOGGER.error("Error sorting channels: %s", e)

        # Update the button configuration with discovered_link info.
        config_file_path = self._hass.config.path("nikobus_button_config.json")
        try:
            if os.path.exists(config_file_path):
                async with aiofiles.open(config_file_path, "r", encoding="utf-8") as file:
                    try:
                        existing_json = json.loads(await file.read())
                        buttons = existing_json.get("nikobus_button", [])
                        if not isinstance(buttons, list):
                            buttons = []
                    except json.JSONDecodeError:
                        buttons = []
            else:
                buttons = []

            # Process new discovered links and add them to the corresponding button without duplicating existing entries.
            for channel_cmds in grouped["channels"].values():
                for cmd in channel_cmds:
                    push_button_addr = cmd.get("push_button_address")
                    for button in buttons:
                        if button.get("address") == push_button_addr:
                            discovered_link_entry = {
                                "module_address": grouped.get("module_address"),
                                "channel": cmd.get("channel"),
                                "mode": cmd.get("mode"),
                                "timer": cmd.get("timer")
                            }
                            if "discovered_link" not in button:
                                button["discovered_link"] = []
                            # Only add the new entry if it doesn't already exist.
                            if discovered_link_entry not in button["discovered_link"]:
                                button["discovered_link"].append(discovered_link_entry)
                                _LOGGER.info("Added new discovered_link to button %s", push_button_addr)
                            else:
                                _LOGGER.debug("Discovered_link already exists for button %s", push_button_addr)
                            break

            updated_config = {"nikobus_button": buttons}
            async with aiofiles.open(config_file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(updated_config, indent=4))
            _LOGGER.info("Updated button configuration written to %s", config_file_path)
        except Exception as e:
            _LOGGER.error("Error updating button configuration: %s", e)

        self._payload_buffer = ""
        self._decoded_buffer = {"module_address": None, "commands": []}
        self._module_address = None
        self._message_complete = False
        self._coordinator.discovery_running = False
        self._coordinator.discovery_module = False

    def get_button_address(self, payload):
        try:
            bin_str = format(int(payload, 16), "024b")
        except Exception as e:
            _LOGGER.error("Error converting button address to binary: %s", e)
            return None
        modified = bin_str[:4] + bin_str[4:6] + bin_str[8:]
        group1 = modified[:6]
        group2 = modified[6:14]
        group3 = modified[14:]
        new_bin = group3 + group2 + group1
        try:
            result_int = int(new_bin, 2)
        except Exception as e:
            _LOGGER.error("Error converting binary to int: %s", e)
            return None
        return format(result_int, "06X")

    def get_push_button_address(self, key_raw, button_address):
        second_part = False
        # Determine the channel count using the provided button address.
        num_channels = self._coordinator.get_button_channels(button_address)
        if num_channels is None:
            if button_address.startswith("0"):
                num_channels = 4
            elif int(button_address[-1], 16) % 2 == 1:
                normalized_address = f"{int(button_address, 16) - 1:06X}"
                num_channels = self._coordinator.get_button_channels(normalized_address)
                if num_channels is not None:
                    _LOGGER.info("Normalized button_address from %s to %s", button_address, normalized_address)
                    button_address = normalized_address
                    second_part = True
                else:
                    _LOGGER.error("Could not determine channels for button address %s or normalized %s", button_address, normalized_address)
                    return None, button_address
            else:
                _LOGGER.error("Could not determine channels for button address %s", button_address)
                return None, button_address

        # Compute the full 6-digit address using your bit-reversal/shifting algorithm.
        converted_result = self._convert_nikobus_address(button_address)
        push_button_address = converted_result["nikobus_address"]

        # Retrieve the mapping for this number of channels from your KEY_MAPPING_MODULE.
        mapping = KEY_MAPPING_MODULE.get(num_channels, {})
        _LOGGER.debug("Debug: key_raw=%s, mapping keys=%s", key_raw, list(mapping.keys()))

        effective_key = key_raw
        if num_channels == 8 and second_part:
            effective_key = key_raw + 4

        if effective_key not in mapping:
            _LOGGER.error("KeyError: effective_key '%s' not found in mapping. Available keys: %s",
                        effective_key, list(mapping.keys()))
            return None, button_address

        # For IR buttons (identified by a button_address starting with "0"),
        # simply return the computed full 6-digit address.
        # if button_address.startswith("0"):
        #    final_push_button_address = push_button_address
        #    _LOGGER.debug(f"IR button: final push button address: {final_push_button_address}")
        # else:
        # Regular processing for non-IR buttons.
        add_value = int(mapping[effective_key], 16)
        original_nibble = int(push_button_address[0], 16)
        new_nibble_value = original_nibble + add_value
        new_nibble_hex = f"{new_nibble_value:X}"
        final_push_button_address = new_nibble_hex + push_button_address[1:]
    
        return final_push_button_address, button_address

    def decode_command_payload(self, payload_hex):
        if not isinstance(payload_hex, str):
            payload_hex = payload_hex.hex().upper()
        payload_hex = payload_hex.upper()

        command_hex = payload_hex[2:6]

        # Get KCTM
        try:
            key_raw, channel_raw, timer_raw, mode_raw = (int(x, 16) for x in command_hex)
        except ValueError:
            _LOGGER.error("Invalid command hex: %s", command_hex)
            return None
        _LOGGER.debug(
            "Command: Key=%s, Channel=%s, Timer=%s, Mode=%s",
            key_raw, channel_raw, timer_raw, mode_raw
        )

        # Get the physical button address
        button_address_hex = payload_hex[6:]
        button_address = self.get_button_address(button_address_hex)
        _LOGGER.debug(f"Converted button address : {button_address}")

        # Get the push button address
        push_button_address, button_address = self.get_push_button_address(key_raw, button_address)

        channel_label = CHANNEL_MAPPING.get(channel_raw, f"Unknown Channel ({channel_raw})")

        module_mappings = {
            'switch_module': (SWITCH_MODE_MAPPING, SWITCH_TIMER_MAPPING),
            'dimmer_module': (DIMMER_MODE_MAPPING, DIMMER_TIMER_MAPPING),
            'roller_module': (ROLLER_MODE_MAPPING, ROLLER_TIMER_MAPPING)
        }

        # Default to 'switch_module' if the module type isn't found
        mode_mapping, timer_mapping = module_mappings.get(self._module_type, module_mappings['switch_module'])

        mode_label = mode_mapping.get(mode_raw, f"Unknown Mode ({mode_raw})")

        if mode_raw in [5, 6]:
            timer_val = timer_mapping.get(timer_raw, ["Unknown"])[0]
        elif mode_raw in [8, 9]:
            timer_val = timer_mapping.get(timer_raw, ["Unknown"])[1]
        elif mode_raw in [1, 2]:
            timer_val = timer_mapping.get(timer_raw, ["Unknown"])[2]
        else:
            timer_val = None

        return {
            "payload": payload_hex,
            "button_address": button_address,
            "push_button_address": push_button_address,
            "K": f"{key_raw}",
            "C": f"{channel_label}",
            "T": f"{timer_val}",
            "M": f"{mode_label}",
        }