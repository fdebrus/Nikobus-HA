import logging

import os
import json
import aiofiles

from .nkbprotocol import make_pc_link_inventory_command
from .const import DEVICE_TYPES, MODE_MAPPING, TIMER_MAPPING, KEY_MAPPING, KEY_MAPPING2, CHANNEL_MAPPING

_LOGGER = logging.getLogger(__name__)

class NikobusDiscovery:
    def __init__(self, hass, coordinator):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._hass = hass
        self._payload_buffer = ""
        self._chunks = []
        self._module_address = None
        self._message_complete = False

    def _classify_device_type(self, device_type_hex):
        """Classify the device type based on the device type hex value."""
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
            # Convert the address string from hexadecimal to an integer.
            address = int(address_string, 16)
        
            # Reverse the order of the lower 21 bits.
            nikobus_address = 0
            for i in range(21):
                nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)
        
            # Shift left by one bit (to form a 22-bit value).
            nikobus_address <<= 1
        
            # Extract the "button" (bits 21 to 23).
            button = (address >> 21) & 0x07
        
            # Format the nikobus address as a 6-digit hexadecimal string and map the button.
            return {"nikobus_address": f"{nikobus_address:06X}", "button": self.map_button(button)}
            
        except ValueError:
            return f"[{address_string}]"

    def map_button(self, button: int) -> str:
        mapping = {
            0: "1",
            1: "5",
            2: "2",
            3: "6",
            4: "3",
            5: "7",
            6: "4",
            7: "8"
        }
        return mapping.get(button, "?")

# OPTIMIZED
    async def query_module_inventory(self, device_address):
        """
        Generates and sends module commands to get Nikobus inventory.
        The full command is built as:
        "$1410" + device_address + <command_code> + "04" + <CRC>
        """
        base_command = f"10{device_address}"
        command_range = range(0x10, 0x1F) if self._coordinator.discovery_module else range(0xA3, 0xFF)
    
        # Sequential execution (order matters)
        for cmd in command_range:
            partial_hex = f"{base_command}{cmd:02X}04"
            pc_link_command = make_pc_link_inventory_command(partial_hex)
            await self._coordinator.nikobus_command.queue_command(pc_link_command)

# OPTIMIZED
    async def process_mode_button_press(self, message):
        # Remove the leading "$" and convert the remaining hex string to bytes.
        stripped_message = message.lstrip("$")
        payload_bytes = bytes.fromhex(stripped_message)

        # Extract the device type from byte at index 5 and classify it.
        device_type_hex = f"{payload_bytes[5]:02X}"
        device_info = self._classify_device_type(device_type_hex)

        # Convert the address by extracting bytes 1 and 2, reversing them, and converting to uppercase hex.
        converted_address = payload_bytes[1:3][::-1].hex().upper()

        # If the device is already discovered, simply update the module data.
        if converted_address in self.discovered_devices:
            await self.update_module_data()
            return

        # Build the channels list based on the number of channels.
        num_channels = int(device_info.get("Channels", 0))
        channels = [
            {"description": f"{device_info['Name']} Output {i + 1}"}
            for i in range(num_channels)
        ]

        # Add the new device to the discovered devices.
        self.discovered_devices[converted_address] = {
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

# OPTIMIZED
    async def parse_inventory_response(self, payload):
        try:
            # Normalize payload: Remove a leading "$0510$" if present, then any extra "$".
            if payload.startswith("$0510$"):
                payload = payload[6:]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)

            # Extract device type and classify.
            device_type_hex = f"{payload_bytes[7]:02X}"
            device_info = self._classify_device_type(device_type_hex)
            category = device_info.get("Category", "Unknown")
            name = device_info.get("Name", "Unknown")
            model = device_info.get("Model", "N/A")
            channels = device_info.get("Channels", 0)

            # Determine the slice to extract the address.
            slice_end = 13 if category == "Module" else 14
            converted_address = payload_bytes[11:slice_end][::-1].hex().upper()

            # Exit early if the payload is invalid.
            if "FFFFFF" in converted_address or "FF" in device_type_hex:
                return

            if category == "Unknown":
                _LOGGER.warning(
                    "Unknown device detected: Type %s at Address %s. "
                    "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                    device_type_hex,
                    converted_address,
                )
                return

            # Add device if it hasn't been discovered yet.
            if converted_address not in self.discovered_devices:
                base_device = {
                    "description": name,
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

            # Update the data for the appropriate device type.
            if category == "Module":
                await self.update_module_data()
            elif category == "Button":
                await self.update_button_data()

        except Exception as e:
            _LOGGER.error("Failed to parse Nikobus payload: %s", e)

# OPTIMIZED
    async def update_module_data(self) -> None:
        """
        Organize discovered devices into module types for Home Assistant.
        """
        module_data = {
            "switch_module": {},
            "dimmer_module": {},
            "roller_module": {},
            "other_module": {},
        }

        for device in self.discovered_devices.values():
            # Skip devices with category "Button"
            if device.get("category") == "Button":
                continue

            address = device.get("address")
            description = device.get("description", "")

            if "Switch Module" in description or "Compact Switch Module" in description:
                module_data["switch_module"][address] = device
            elif "Dimmer Module" in description:
                module_data["dimmer_module"][address] = device
            elif "Roller Shutter Module" in description:
                # Update each channel's operation time
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

# OPTIMIZED
    async def update_button_data(self):
        """
        Update or create the nikobus_button_config.json file with discovered button data.
        """
        file_path = self._hass.config.path("nikobus_button_config.json")

        # Load existing data if the file exists
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

        # Make a working copy of the existing list of button entries.
        updated_data = existing_data.copy()
        # Create a lookup for fast access by discovered channel address.
        lookup = {button.get("address"): button for button in updated_data if "address" in button}

        # Process each discovered button
        for device_address, device in self.discovered_devices.items():
            if device.get("category") != "Button":
                continue

            description = device.get("description", "")
            model = device.get("model", "")
            num_channels = device.get("channels", 0)

            # Determine the list of keys based on the number of channels
            if num_channels == 2:
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

            # Compute the converted address once per device.
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

            # Save the computed channels_data with the discovered device.
            device["channels_data"] = channels_data

            # For each channel, update an existing button entry or add a new one.
            for channel_info in channels_data.values():
                discovered_channel_address = channel_info["address"]
                key = channel_info["key"]

                new_info = {
                    "key": key,
                    "type": description,
                    "model": model,
                    "address": device_address,
                    "channels": num_channels,
                }

                # Use lookup for efficiency
                button = lookup.get(discovered_channel_address)
                if button:
                    discovered_list = button.setdefault("discovered_info", [])
                    # Find if there is already an entry matching new_info
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
                        "description": description,
                        "address": discovered_channel_address,
                        "impacted_module": [{"address": "", "group": ""}],
                        "discovered_info": [new_info]
                    }
                    updated_data.append(new_button)
                    lookup[discovered_channel_address] = new_button

        # Write the updated data back to the file in the same JSON structure.
        try:
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                output_json = {"nikobus_button": updated_data}
                await file.write(json.dumps(output_json, indent=4))
            _LOGGER.info("Updated button data written to file: %s", file_path)
        except Exception as e:
            _LOGGER.error("Failed to write button data to file: %s", e)


### MORE WORK HERE BELOW ###

    async def parse_module_inventory_response(self, message):
        """
        Called for each incoming line. This method:
        1. Removes the header and CRC.
        2. Extracts the module address (from the first valid line) and payload data.
        3. If the message is already marked complete (_message_complete==True),
        new lines are ignored.
        4. Otherwise, appends the payload data to an internal buffer and splits it
        into 12-character chunks.
        5. As soon as a chunk equals "FFFFFFFFFFFF", it calls process_complete_message()
        and resets the buffers.
        """
        if self._message_complete:
            _LOGGER.debug("Message already complete; ignoring new input.")
            return

        _LOGGER.debug("Received message: %r", message)
        header = "$0510$2E"
        if not message.startswith(header):
            _LOGGER.error("Message does not start with expected header.")
            return

        # Remove header and CRC (last 6 hex characters)
        data_with_crc = message[len(header):]
        if len(data_with_crc) < 6:
            _LOGGER.error("Data too short after header removal.")
            return
        data = data_with_crc[:-6]

        # Ensure there's enough data to extract the module address (first 4 characters)
        if len(data) < 4:
            _LOGGER.error("Data too short to extract module address.")
            return
        module_address = data[:4]
        if self._module_address is None:
            self._module_address = module_address

        # The remaining data is payload for the button.
        payload_data = data[4:]
        _LOGGER.debug("Appending payload data: %r", payload_data)
        self._payload_buffer += payload_data

        # Process the buffer into complete 12-character chunks.
        while len(self._payload_buffer) >= 12:
            chunk = self._payload_buffer[:12]
            self._chunks.append(chunk)
            _LOGGER.debug("Extracted chunk: %r", chunk)
            self._payload_buffer = self._payload_buffer[12:]
            if chunk.strip().upper() == "FFFFFFFFFFFF":
                _LOGGER.debug("Termination chunk encountered: %r", chunk)
                self._message_complete = True
                await self.process_complete_message()
                return

    async def process_complete_message(self):
        """
        Processes all accumulated complete chunks as one message.   
        If a termination chunk is encountered in the list, only chunks before it are processed.
        After processing, the internal state is reset.
        """
        if not self._chunks:
            _LOGGER.info("No complete chunks to process.")
            return

        # Determine the termination index.
        termination_index = None
        for i, chunk in enumerate(self._chunks):
            if chunk.strip().upper() == "FFFFFFFFFFFF":
                termination_index = i
                _LOGGER.debug("Termination chunk encountered at index %d.", i)
                break

        if termination_index is not None:
            chunks_to_process = self._chunks[:termination_index]
        else:
            chunks_to_process = self._chunks

        _LOGGER.debug("Processing complete message with %d chunks.", len(chunks_to_process))
        new_commands = []
        for chunk in chunks_to_process:
            _LOGGER.debug("Decoding chunk: %r", chunk)
            reversed_chunk = self._reverse_hex(chunk)
            decoded = self.decode_command_payload(reversed_chunk)
            if decoded is not None:
                new_commands.append(decoded)
            else:
                _LOGGER.error("Failed to decode chunk: %r", chunk)

        # Use an in-memory buffer to accumulate decoded commands.
        if not hasattr(self, "_decoded_buffer"):
            self._decoded_buffer = {
                "module_address": self._module_address,
                "commands": []
            }
        else:
            if self._decoded_buffer.get("module_address") is None:
                self._decoded_buffer["module_address"] = self._module_address

        # Append the new commands to the in-memory buffer.
        self._decoded_buffer["commands"].extend(new_commands)

        _LOGGER.info("Decoded Button Commands:")
        _LOGGER.info("module_address: %s", self._decoded_buffer["module_address"])
        for idx, cmd in enumerate(self._decoded_buffer["commands"], start=1):
            _LOGGER.info(
                "Command %d: Payload: %s, Button Address: %s, Push Button Address: %s, Key: %s, Channel: %s, Timer: %s, Mode: %s",
                idx, cmd["payload"], cmd["button_address"],
                cmd["push_button_address"], cmd["K"], cmd["C"], cmd["T"], cmd["M"]
            )

        # Transform the in-memory buffer into the new format.
        # Group commands by their "C" key (channel), and remove that key from each command.
        grouped = {
            "module_address": self._decoded_buffer["module_address"],
            "channels": {}
        }
        for cmd in self._decoded_buffer["commands"]:
            channel = cmd["C"]  # e.g., "Channel 1"
            # Create a new command dict with only the desired keys.
            new_cmd = {
                "button_address": cmd["button_address"],
                "push_button_address": cmd["push_button_address"],
                "button_key": cmd["K"],
                "timer": cmd["T"],
                "mode": cmd["M"]
            }
            if channel not in grouped["channels"]:
                grouped["channels"][channel] = []
            grouped["channels"][channel].append(new_cmd)

        # Sort the channels by their numeric part.
        try:
            sorted_channels = dict(
                sorted(
                    grouped["channels"].items(),
                    key=lambda x: int(x[0].split()[1]) if len(x[0].split()) > 1 and x[0].split()[1].isdigit() else 0
                )
            )
            grouped["channels"] = sorted_channels
        except Exception as e:
            _LOGGER.error("Error sorting channels: %s", e)

        # Write the transformed data to file.
        file_path = self._hass.config.path("nikobus_button_discovered_relationship.json")
        try:
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(grouped, indent=4))
            _LOGGER.info("Decoded message written to %s", file_path)
        except Exception as e:
            _LOGGER.error("Error writing decoded message to file: %s", e)

        # Reset message-related internal state (keeping the in-memory _decoded_buffer intact).
        self._payload_buffer = ""
        self._chunks = []
        self._module_address = None
        self._message_complete = False
        
    def decode_command_payload(self, payload_hex):
        """
        Decodes a 12-character button payload (expected to be reversed from the received chunk)
        into its constituent components.
        """
        if not isinstance(payload_hex, str):
            payload_hex = payload_hex.hex().upper()
        payload_hex = payload_hex.upper()

        if len(payload_hex) != 12:
            _LOGGER.error("Unexpected payload length: %s", payload_hex)
            return None

        command_hex = payload_hex[2:6]
        button_address_hex_part = payload_hex[6:]
        _LOGGER.debug("Command portion (hex): %s", command_hex)
        _LOGGER.debug("Button address portion (hex): %s", button_address_hex_part)

        try:
            bin_str = format(int(button_address_hex_part, 16), "024b")
        except Exception as e:
            _LOGGER.error("Error converting button address to binary: %s", e)
            return None

        _LOGGER.debug("Full 24-bit Binary: %s", bin_str)
        modified = bin_str[:4] + bin_str[4:6] + bin_str[8:]
        _LOGGER.debug("Modified (22-bit) Address: %s", modified)
        group1 = modified[:6]
        group2 = modified[6:14]
        group3 = modified[14:]
        new_bin = group3 + group2 + group1
        _LOGGER.debug("Reassembled new_bin: %s", new_bin)

        try:
            result_int = int(new_bin, 2)
        except Exception as e:
            _LOGGER.error("Error converting binary to int: %s", e)
            return None

        button_address = format(result_int, "06X")
        result = self._convert_nikobus_address(button_address)
        push_button_address = result["nikobus_address"]
        button = result["button"]
        _LOGGER.debug("Converted address %s, button %s", push_button_address, button)

        command_rev_hex = command_hex.upper()
        _LOGGER.debug("Command Rev Hex: %s", command_rev_hex)
        try:
            key_raw, channel_raw, timer_raw, mode = (int(x, 16) for x in command_rev_hex)
        except ValueError:
            _LOGGER.error("Invalid command hex: %s", command_rev_hex)
            return None

        _LOGGER.debug("Command %s K %s C %s T %s M %s", command_rev_hex, key_raw, channel_raw, timer_raw, mode)
        mode_description = MODE_MAPPING.get(mode, f"Unknown Mode ({mode})")
        if mode in [5, 6]:
            timer_val = TIMER_MAPPING.get(timer_raw, ["Unknown"])[0]
        elif mode in [8, 9]:
            timer_val = TIMER_MAPPING.get(timer_raw, ["Unknown"])[1]
        elif mode in [1, 2]:
            timer_val = TIMER_MAPPING.get(timer_raw, ["Unknown"])[2]
        else:
            timer_val = None

        channel = CHANNEL_MAPPING.get(channel_raw, f"Unknown Channel ({channel_raw})")
        key = KEY_MAPPING2.get(key_raw, f"Unknown Key ({key_raw})")

        return {
            "payload": payload_hex,
            "button_address": button_address,
            "push_button_address": push_button_address,
            "K": key,
            "C": channel,
            "T": timer_val,
            "M": mode_description,
            "raw_command_reversed_hex": command_rev_hex,
        }

    def _reverse_hex(self, hex_str):
        b = bytes.fromhex(hex_str)
        reversed_b = b[::-1]
        return reversed_b.hex().upper()
