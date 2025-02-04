import logging

import json
import binascii

from .nkbprotocol import make_pc_link_inventory_command

from .const import DEVICE_TYPES

_LOGGER = logging.getLogger(__name__)

class NikobusDiscovery:
    def __init__(self, coordinator):
        self.discovered_devices = {}
        self._coordinator = coordinator

    def classify_device_type(self, device_type_hex):
        """ Classify the device type based on the device type hex value. """
        return DEVICE_TYPES.get(device_type_hex, {
            "Category": "Unknown", "Model": "Unknown", "Channels": 0, "Name": "Unknown Device"
        })

#
# Received a request to dump PC Link Data, Loop till FF for now, need to optimize when no data to stop earlier       
#
    async def query_pc_link_module(self, device_address):
        """
        Generates and sends PC Link commands to get Nikobus inventory
        The full command is built as:
        "$1410" + device_address + <command_code> + "04" + <CRC>
        """
        for cmd in range(0xA3, 0xFF):
            # Format the command code as two uppercase hexadecimal digits.
            command_code = f"{cmd:02X}"

            # Construct the partial command (without CRC) according to the protocol:
            # Header (10) + Module Address + Command Code + Fixed Part (04)
            partial_hex = f"10{device_address}{command_code}04"

            # Generate the full command by appending the CRC etc.
            pc_link_command = make_pc_link_inventory_command(partial_hex)

            # Send the command asynchronously.
            await self._coordinator.nikobus_command.queue_command(pc_link_command)

#
# Received an inventory response from PC Link data
#

    async def parse_inventory_response(self, payload):
        try:
            if payload.startswith("$0510$"):
                payload = payload[6:] 
    
            # Remove any additional "$" at the start if necessary
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)

            converted_address = payload_bytes[11:14][::-1].hex().upper()
            _LOGGER.debug(f"Processed address: {converted_address}")

            device_type_hex = format(payload_bytes[7], '02X')
            _LOGGER.debug(f"Extracted device type (hex): {device_type_hex}")

            device_info = self.classify_device_type(device_type_hex)
            _LOGGER.debug(f"Classified device type: {device_info}")

            if device_info["Category"] == "Unknown":
                _LOGGER.warning(
                    f"Unknown device detected: Type {device_type_hex} at Address {converted_address}. "
                    "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information."
                )
                return

            _LOGGER.info(f"Discovered {device_info['Category']} - {device_info['Name']}, Model: {device_info.get('Model', 'N/A')}, at Address: {converted_address}")

        except Exception as e:
            _LOGGER.error(f"Failed to parse Nikobus payload: {e}")

#
# A yellow "Mode Button" has been pressed on a module, identify and report the module
#

    async def process_mode_button_message(self, message):
        payload = message.lstrip("$")
        payload_bytes = bytes.fromhex(payload)

        device_type_hex = format(payload_bytes[5], '02X')
        device_info = self.classify_device_type(device_type_hex)

        converted_address = payload_bytes[1:3][::-1].hex().upper()

        if converted_address not in self.discovered_devices:
            num_channels = int(device_info["Channels"]) if "Channels" in device_info else 0

            self.discovered_devices[converted_address] = {
                "description": f"{device_info['Name']} at {converted_address}",
                "model": device_info["Model"],
                "address": converted_address,
                "channels": [
                    {"description": f"{device_info['Name']} Output {i+1}", "led_on": "", "led_off": ""}
                    for i in range(num_channels)
                ]
            }

            _LOGGER.info(f"Discovered device: {device_type_hex} {device_info} at {converted_address} from message: {payload}")

        return self.generate_module_json()

    def generate_module_json(self):
        output_structure = {
            "switch_module": [],
            "dimmer_module": [],
            "roller_module": []
        }

        for device in self.discovered_devices.values():
            if "Switch Module" in device["description"] or "Compact Switch Module" in device["description"]:
                output_structure["switch_module"].append(device)
            elif "Dimmer Module" in device["description"]:
                output_structure["dimmer_module"].append(device)
            elif "Roller Shutter Module" in device["description"]:
                for channel in device["channels"]:
                    channel["operation_time"] = "40"
                output_structure["roller_module"].append(device)

        return json.dumps(output_structure, indent=4)

#####################LOT OF WORK BELOW THIS LINE STILL TO BE DONE#################################

    def decode_command_payload(self, payload_bytes):
        """
        Decodes a 10–hex–digit command payload.

        - First 6 hex digits (24 bits): Button address, sent with bits reversed.
        - Last 4 hex digits (16 bits): Command portion, sent with bits reversed.

        The command portion contains:
        - Mode (M): First nibble (4 bits)
        - Timer (T): Full 4 bits, dependent on Mode.
        - Channel (C): Third nibble (4 bits)
        - Key (K): Lower 2 bits of the last nibble.

        """
        _LOGGER.debug(f"Payload: {payload_bytes.hex().upper()}")

        # --- Process the 24-bit button address (first 3 bytes) ---
        addr_bytes = payload_bytes[:3]  # First 3 bytes for button address
        addr_bits = ''.join(f"{byte:08b}" for byte in addr_bytes)  # Convert bytes to binary
        addr_bits_reversed = addr_bits[::-1]  # Reverse all bits
        button_address = format(int(addr_bits_reversed, 2), '06X')  # Convert back to HEX

        # --- Process the 16-bit command portion (last 2 bytes) ---
        command_bytes = payload_bytes[3:]  # Last 2 bytes
        command_bits = ''.join(f"{byte:08b}" for byte in command_bytes)  # Convert bytes to binary
        command_bits_reversed = command_bits[::-1]  # Reverse all bits
        command_rev_hex = format(int(command_bits_reversed, 2), '04X')  # Convert back to HEX

        # Extract Mode, Timer, Channel, and Key
        mode = int(command_rev_hex[0], 16)  # Mode (M)
        timer_raw = int(command_rev_hex[1], 16)  # Timer (T), full 4-bit value
        channel = int(command_rev_hex[2], 16)  # Channel (C)
        key = int(command_rev_hex[3], 16) & 0x3  # Key (K) uses only the lower 2 bits

        # Convert Timer based on Mode
        timer_mapping = {
            0x6: 10, 0x7: 10, 0xB: 0.5, 0xC: 45, 0xD: 60,
            0xE: 90, 0xF: 120, 0x2: 0, 0x3: 0, 0x8: 8, 0x9: 9, 0xA: 15
        }
        timer = timer_mapping.get(mode, timer_raw)  # Default to raw value if mode unknown

        return {
            "button_address": button_address,
            "K": key,
            "C": channel,
            "T": timer,
            "M": mode,
            "raw_command_reversed_hex": command_rev_hex
        }


    def decode_nikobus_payload(self, full_payload):
        """
        Decodes a full Nikobus command string:
        - Type Code (2 hex digits)
        - Header (4 hex digits)
        - Commands (each 10 hex digits)
        """
        _LOGGER.debug(f"Original payload: {full_payload}")

        # Remove known prefixes
        if full_payload.startswith("$0510$"):
            full_payload = full_payload[6:]

        full_payload = full_payload.lstrip("$")  # Remove any additional "$"

        try:
            payload_bytes = bytes.fromhex(full_payload)
        except ValueError:
            _LOGGER.error(f"Invalid hex payload received: {full_payload}")
            return None

        type_code = payload_bytes[:1].hex().upper()  # First byte
        header = payload_bytes[1:3].hex().upper()   # Next 2 bytes
        commands_bytes = payload_bytes[3:]  # Remaining bytes

        # Remove CRC if present (8 hex digits = 4 bytes)
        if len(commands_bytes) % 5 == 4:
            _LOGGER.info("Detected 8 trailing CRC hex digits, ignoring them.")
            commands_bytes = commands_bytes[:-4]

        elif len(commands_bytes) % 5 != 0:
            remainder = len(commands_bytes) % 5
            _LOGGER.warning(f"Command portion has {remainder} extra bytes that will be ignored.")

        # Decode each command payload (5 bytes each)
        n_commands = len(commands_bytes) // 5
        commands = []
        for i in range(0, n_commands * 5, 5):
            cmd_payload = commands_bytes[i:i+5]  # Get 5 bytes directly
            decoded_cmd = self.decode_command_payload(cmd_payload)  # Pass as bytes
            commands.append(decoded_cmd)

        return {
            "type_code": type_code,
            "header": header,
            "commands": commands
        }

    async def process_button_command_payload(self, full_payload):
        """
        Processes a full Nikobus Button command payload and logs the decoded button data.

        Uses the decode_nikobus_payload() helper to decode the payload and then
        prints (via _LOGGER.info) the following information for each command:
          - Button Address
          - Key (K)
          - Channel (C)
          - Timer (T)
          - Mode (M)
        """
        try:
            decoded = self.decode_nikobus_payload(full_payload)
            _LOGGER.info("Decoded Button Commands:")
            _LOGGER.info(f"Type Code: {decoded['type_code']}, Header: {decoded['header']}")
            for idx, cmd in enumerate(decoded['commands'], start=1):
                _LOGGER.info(
                    f"Command {idx}: Button Address: {cmd['button_address']}, "
                    f"Key: {cmd['K']}, Channel: {cmd['C']}, Timer: {cmd['T']}, Mode: {cmd['M']}"
                )
        except Exception as e:
            _LOGGER.error(f"Failed to decode button command payload: {e}")
