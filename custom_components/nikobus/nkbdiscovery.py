import logging

import json
import aiofiles

from .nkbprotocol import make_pc_link_inventory_command

_LOGGER = logging.getLogger(__name__)

DEVICE_TYPES = {
    # Known Device Types
    "01": {
        "Category": "Module",
        "Model": "05-000-02",
        "Channels": 12,
        "Name": "Switch Module",
    },
    "02": {
        "Category": "Module",
        "Model": "05-001-02",
        "Channels": 6,
        "Name": "Roller Shutter Module",
    },
    "03": {
        "Category": "Module",
        "Model": "05-007-02",
        "Channels": 12,
        "Name": "Dimmer Module",
    },
    "04": {
        "Category": "Button",
        "Model": "05-342",
        "Channels": 2,
        "Name": "Button with 2 Operation Points",
    },
    "06": {
        "Category": "Button",
        "Model": "05-346",
        "Channels": 4,
        "Name": "Button with 4 Operation Points",
    },
    "08": {
        "Category": "Module", 
        "Model": "05-201", 
        "Name": 
        "PC Logic"
    },
    "09": {
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact Switch Module",
    },
    "0A": {
        "Category": "Module", 
        "Model": "05-200", 
        "Name": "PC Link"
    },
    "0C": {
        "Category": "Button",
        "Model": "05-348",
        "Channels": 4,
        "Name": "IR Button with 4 Operation Points",
    },
    "12": {
        "Category": "Button",
        "Model": "05-349",
        "Channels": 8,
        "Name": "Button with 8 Operation Points",
    },
    "1F": {
        "Category": "Button",
        "Model": "05-311",
        "Channels": 2,
        "Name": "RF Transmitter with 2 Operation Points",
    },
    "23": {
        "Category": "Button",
        "Model": "05-312",
        "Channels": 4,
        "Name": "RF Transmitter with 4 Operation Points",
    },
    "25": {
        "Category": "Button", 
        "Model": "05-055", 
        "Name": 
        "All-Function Interface"
    },
    "31": {
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact Switch Module??",
    },
    "3F": {
        "Category": "Button",
        "Model": "05-344",
        "Channels": 2,
        "Name": "Feedback Button with 2 Operation Points",
    },
    "40": {
        "Category": "Button",
        "Model": "05-347",
        "Channels": 4,
        "Name": "Feedback Button with 4 Operation Points",
    },
    "42": {
        "Category": "Module", 
        "Model": "05-207", 
        "Name": "Feedback Module"
    },
    "44": {
        "Category": "Button", 
        "Model": "05-057", 
        "Name": "Switch Interface"
    },
}

MODE_MAPPING = {
    0: "M01 On/Off",
    1: "M02 On with operating time",
    2: "M03 Off with operation time",
    3: "M04 Pushbutton",
    4: "M05 Impulse",
    5: "M06 Delayed off (long up to 2h)",
    6: "M07 Delayed on (long up to 2h)",
    7: "M08 Flashing",
    8: "M11 Delayed off (short up to 50s)",
    9: "M12 Delayed on (short up to 50s)",
    11: "M14 Light scene on",
    12: "M15 Light scene on / off",
}

TIMER_MAPPING = {
    0: ["10s", "0.5s", "0s"],
    1: ["1m", "1s", "1s"],
    2: ["2m", "2s", "2s"],
    3: ["3m", "3s", "3s"],
    4: ["4m", "4s", None],
    5: ["5m", "5s", None],
    6: ["6m", "6s", None],
    7: ["7m", "7s", None],
    8: ["8m", "8s", None],
    9: ["9m", "9s", None],
    10: ["15m", "15s", None],
    11: ["30m", "20s", None],
    12: ["45m", "25s", None],
    13: ["60m", "30s", None],
    14: ["90m", "40s", None],
    15: ["120m", "50s", None],
}

KEY_MAPPING = {
    2: {"1A": "8", "1B": "C"},
    4: {"1A": "8", "1B": "C", "1C": "0", "1D": "4"},
    8: {
        "1A": "A",
        "1B": "E",
        "1C": "2",
        "1D": "6",
        "2A": "8",
        "2B": "C",
        "2C": "0",
        "2D": "4",
    },
}

KEY_MAPPING2 = {0: "1C", 1: "1A", 2: "1D", 3: "1B", 4: "2C", 5: "2A", 6: "2D", 7: "2B"}

CHANNEL_MAPPING = {
    0: "Channel 1",
    1: "Channel 2",
    2: "Channel 3",
    3: "Channel 4",
    4: "Channel 5",
    5: "Channel 6",
    6: "Channel 7",
    7: "Channel 8",
    8: "Channel 9",
    9: "Channel 10",
    10: "Channel 11",
    11: "Channel 12",
}


class NikobusDiscovery:
    def __init__(self, hass, coordinator):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._hass = hass

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

    def _reverse_hex(self, hex_str):
        b = bytes.fromhex(hex_str)
        reversed_b = b[::-1]
        return reversed_b.hex().upper()

    def _convert_nikobus_address(self, address_string: str) -> str:
        # Extract the lower 21 bits.
        address = int(address_string, 16)
        lower21 = address & ((1 << 21) - 1)

        # Reverse bits 0..20.
        reversed_bits = 0
        for _ in range(21):
            reversed_bits = (reversed_bits << 1) | (lower21 & 1)
            lower21 >>= 1

        # Shift left by 1 to produce the final 22-bit value.
        nikobus_address = reversed_bits << 1

        # Extract the "button" (bits 21..23). Needed ?
        button = (address >> 21) & 0x07

        # Format the nikobus address as a 6-digit hexadecimal string and map the button.
        return {"nikobus_address": f"{nikobus_address:06X}", "button": button}

    #
    # Received a request to dump a module Data, Loop till FF for now, need to optimize when no data to stop earlier (todo)
    #
    async def query_module_inventory(self, device_address):
        """
        Generates and sends module commands to get Nikobus inventory.
        The full command is built as:
        "$1410" + device_address + <command_code> + "04" + <CRC>
        """
        base_command = f"10{device_address}"
        command_range = range(0x10, 0x1F) if self._coordinator.discovery_module else range(0xA3, 0xFF)

        for cmd in command_range:
            partial_hex = f"{base_command}{cmd:02X}04"
            pc_link_command = make_pc_link_inventory_command(partial_hex)
            await self._coordinator.nikobus_command.queue_command(pc_link_command)

    #
    # A yellow "Mode Button" has been pressed on a module, identify and report the module
    #
    async def process_mode_button_press(self, message):
        # Remove the leading "$" once and convert the remaining hex string to bytes.
        stripped_message = message.lstrip("$")
        payload_bytes = bytes.fromhex(stripped_message)

        # Extract device type from byte at index 5 as a two-digit hex string.
        device_type_hex = f"{payload_bytes[5]:02X}"
        device_info = self._classify_device_type(device_type_hex)

        # Convert the address:
        # Extract bytes 1 and 2, reverse them, and convert to uppercase hex.
        converted_address = payload_bytes[1:3][::-1].hex().upper()

        # If the device is not yet discovered, add it.
        if converted_address not in self.discovered_devices:
            num_channels = int(device_info.get("Channels", 0))
            channels = [
                {
                    "description": f"{device_info['Name']} Output {i + 1}",
                    "led_on": "",
                    "led_off": "",
                }
                for i in range(num_channels)
            ]

            self.discovered_devices[converted_address] = {
                "description": f"{device_info['Name']} at {converted_address}",
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

    #
    # Received an inventory response from PC Link data
    #

    async def parse_inventory_response(self, payload):
        try:
            # Normalize payload: Remove a leading "$0510$" if present, then any extra "$".
            if payload.startswith("$0510$"):
                payload = payload[6:]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)

            # Extract and classify the device type.
            device_type_hex = f"{payload_bytes[7]:02X}"
            if "FF" in device_type_hex:
                return

            _LOGGER.debug("Extracted device type (hex): %s", device_type_hex)
            device_info = self._classify_device_type(device_type_hex)
            _LOGGER.debug("Classified device type: %s", device_info)

            # Cache device properties for reuse.
            category = device_info.get("Category", "Unknown")
            name = device_info.get("Name", "Unknown")
            model = device_info.get("Model", "N/A")
            channels = device_info.get("Channels", 0)

            # Determine the slice to extract the address.
            # For Modules, use 2 bytes (slice_end=13); for others (e.g. Button) use 3 bytes (slice_end=14).
            slice_end = 13 if category == "Module" else 14
            converted_address = payload_bytes[11:slice_end][::-1].hex().upper()

            _LOGGER.debug("Processed address: %s", converted_address)

            # Warn and exit if the device category is unknown.
            if category == "Unknown":
                _LOGGER.warning(
                    "Unknown device detected: Type %s at Address %s. "
                    "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                    device_type_hex,
                    converted_address,
                )
                return

            # Add device to discovered_devices if it is not already known.
            if converted_address not in self.discovered_devices:
                base_device = {
                    "description": f"{name} at {converted_address}",
                    "model": model,
                    "address": converted_address,
                    "channels": channels,
                }
                if category == "Module":
                    num_channels = int(device_info.get("Channels", 0))
                    base_device["channels"] = [
                        {
                            "description": f"{name} Output {i + 1}",
                            "led_on": "",
                            "led_off": "",
                        }
                        for i in range(num_channels)
                    ]
                elif category == "Button":
                    base_device["impacted_module"] = [{"address": "xxxx", "group": "x"}]

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
            description = device.get("description", "")
            address = device.get("address")
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
            file_path = self._hass.config.path("nikobus_module_discovery.json")
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(module_data, indent=4))
            _LOGGER.info("Module data written to file: %s", file_path)
        except Exception as e:
            _LOGGER.error("Failed to write module data to file: %s", e)

        # Update the coordinator's data structure.
        # self._coordinator.dict_module_data = module_data

    async def update_button_data(self) -> None:
        """
        Organize discovered push button devices into the nikobus_button JSON structure,
        including computed per-channel data.
        """
        button_data = {
            "nikobus_button": {},
        }

        # Process each discovered device that matches the criteria.
        for device in self.discovered_devices.values():
            description = device.get("description", "")
            address = device.get("address")
            num_channels = device.get("channels")

            # Process only devices that are Buttons.
            if "Button" in description:
                # Compute the base push button address and the button value.
                result = self._convert_nikobus_address(address)
                pb_address = result["nikobus_address"]
                button = result["button"]
                _LOGGER.debug(f"Address {pb_address} button {button}")

                # Determine the list of keys based on the number of channels.
                if num_channels == 2:
                    keys = ["1A", "1B"]
                elif num_channels == 4:
                    keys = ["1A", "1B", "1C", "1D"]
                elif num_channels == 8:
                    keys = ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"]
                else:
                    _LOGGER.error(
                        f"Unexpected number of channels: {num_channels} for device {address}"
                    )
                    continue

                # Get the mapping for the current number of channels.
                mapping = KEY_MAPPING[num_channels]

                # Compute the channel-specific data.
                channels_data = {}
                for idx, key in enumerate(keys, start=1):
                    new_nibble = mapping[key]
                    # Replace the first nibble of pb_address with the new nibble.
                    updated_addr = new_nibble + pb_address[1:]
                    channels_data[f"channel_{idx}"] = {
                        "key": key,
                        "address": updated_addr,
                    }
                    _LOGGER.info(
                        f"Channel {idx} (Key {key}) for device {address}: {updated_addr}"
                    )

                # Add the computed channels data to the device dictionary.
                device["channels_data"] = channels_data

                # Add or update the button entry in the main JSON structure.
                button_data["nikobus_button"][address] = device

        # Save the button data to a file.
        try:
            file_path = self._hass.config.path("nikobus_button_discovery.json")
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(button_data, indent=4))
            _LOGGER.info("Button data written to file: %s", file_path)
        except Exception as e:
            _LOGGER.error("Failed to write button data to file: %s", e)

        # Update the coordinator's data structure.
        # self._coordinator.dict_button_data = button_data

    async def parse_module_inventory_response(self, full_payload):
        """
        Processes a full Nikobus Button command payload and logs the decoded button data.
        For each command, the following is logged:
            - Button Address
            - Key (K)
            - Channel (C)
            - Timer (T)
            - Mode (M)
        """
        try:
            decoded = self.decode_nikobus_payload(full_payload)
            if decoded is None:
                _LOGGER.info("No valid commands to process.")
                return

            _LOGGER.info("Decoded Button Commands:")
            _LOGGER.info(
                f"Type Code: {decoded['type_code']}, module_address: {decoded['module_address']}"
            )

            for idx, cmd in enumerate(decoded["commands"], start=1):
                _LOGGER.info(
                    f"Command {idx}: Button Address: {cmd['button_address']}, Push Button Address: {cmd['push_button_address']}, "
                    f"Key: {cmd['K']}, Channel: {cmd['C']}, Timer: {cmd['T']}, Mode: {cmd['M']}"
                )

            # Initialize discovered_relationship if it doesn't exist.
            if not hasattr(self, "discovered_relationship"):
                self.discovered_relationship = []

            # Accumulate new commands.
            self.discovered_relationship.extend(decoded["commands"])

            # Dump the complete discovered_relationship list to the file.
            file_path = self._hass.config.path(
                "nikobus_button_discovered_relationship.json"
            )
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(self.discovered_relationship, indent=4))

        except Exception as e:
            _LOGGER.error(f"Failed to decode button command payload: {e}")

    def decode_nikobus_payload(self, full_payload):
        _LOGGER.debug(f"Original payload: {full_payload}")

        full_payload = full_payload[6:-2]
        full_payload = self._reverse_hex(full_payload)
        payload_bytes = bytes.fromhex(full_payload)

        _LOGGER.debug(f"Converted payload: {payload_bytes.hex().upper()}")

        # Extract type code and module_address
        type_code = payload_bytes[-1:].hex().upper()
        module_address = payload_bytes[-3:-1][::-1].hex().upper()
        commands_bytes = payload_bytes[:-3]

        _LOGGER.debug(f"Type code: {type_code}")
        _LOGGER.debug(f"module_address: {module_address}")
        _LOGGER.debug(f"Commands (hex): {commands_bytes.hex().upper()}")

        # Each command is 6 bytes (12 hex digits)
        n_commands = len(commands_bytes) // 6
        commands = []
        for i in range(0, n_commands * 6, 6):
            cmd_payload = commands_bytes[i : i + 6]
            cmd_payload_hex = cmd_payload.hex().upper()
            _LOGGER.debug(f"Command (payload): {cmd_payload_hex}")
            decoded_cmd = self.decode_command_payload(cmd_payload_hex)
            if decoded_cmd is None:
                _LOGGER.info("Command skipped due to FFFFFF in button address.")
                return None
            commands.append(decoded_cmd)

        return {
            "type_code": type_code,
            "module_address": module_address,
            "commands": commands,
        }

    def decode_command_payload(self, payload_hex: str):
        # Ensure payload_hex is a hex string in uppercase.
        if not isinstance(payload_hex, str):
            payload_hex = payload_hex.hex().upper()
        payload_hex = payload_hex.upper()

        if len(payload_hex) != 12:
            _LOGGER.error("Unexpected payload length: %s", payload_hex)
            return None

        # Extract portions: the command portion and the button address portion.
        command_hex = payload_hex[2:6]
        button_address_hex_part = payload_hex[6:]

        if "FFFFFF" in button_address_hex_part:
            _LOGGER.info(
                "Skipping command because button_address_hex_part contains FFFFFF: %s",
                payload_hex,
            )
            return None

        _LOGGER.debug("Command portion (hex): %s", command_hex)
        _LOGGER.debug("Button address portion (hex): %s", button_address_hex_part)

        # Convert the 6-digit hex string to a 24-bit binary string.
        bin_str = format(int(button_address_hex_part, 16), "024b")
        _LOGGER.debug("Full 24-bit Binary: %s", bin_str)

        # Drop two bits from the second nibble:
        #   Keep nibble1 (first 4 bits), first 2 bits of nibble2, then all of nibbles 3–6.
        modified = bin_str[:4] + bin_str[4:6] + bin_str[8:]
        _LOGGER.debug("Modified (22-bit) Address: %s", modified)

        # Partition the modified 22-bit string into three groups:
        #   group1: first 6 bits, group2: next 8 bits, group3: final 8 bits.
        group1 = modified[:6]
        group2 = modified[6:14]
        group3 = modified[14:]
        new_bin = group3 + group2 + group1
        _LOGGER.debug("Reassembled new_bin: %s", new_bin)

        # Convert back to integer and then to a 6-digit hex string.
        result_int = int(new_bin, 2)
        button_address = format(result_int, "06X")
        result = self._convert_nikobus_address(button_address)
        push_button_address = result["nikobus_address"]
        button = result["button"]
        _LOGGER.debug("Address %s button %s", push_button_address, button)

        # Process the command portion.
        # (Using command_hex directly guarantees a 4–character uppercase string.)
        command_rev_hex = command_hex.upper()
        _LOGGER.debug("Command Rev Hex: %s", command_rev_hex)

        try:
            key_raw, channel_raw, timer_raw, mode = (int(x, 16) for x in command_rev_hex)
        except ValueError:
            _LOGGER.error("Invalid command hex: %s", command_rev_hex)
            return None

        _LOGGER.debug("K %s C %s T %s M %s", key_raw, channel_raw, timer_raw, mode)

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
            "button_address": button_address,
            "push_button_address": push_button_address,
            "K": key,
            "C": channel,
            "T": timer_val,
            "M": mode_description,
            "raw_command_reversed_hex": command_rev_hex,
        }