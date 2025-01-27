import logging

_LOGGER = logging.getLogger(__name__)

def parse_response(response):
    """Parse the Nikobus response and decode its configuration."""
    _LOGGER.debug("Parsing response: %s", response)
    parsed_data = []

    # Preprocess the response
    try:
        response = response.replace("$", "").replace(" ", "")  # Clean input
        if response.startswith("2E"):  # If $2E is a header, remove it explicitly
            response = response[2:]  # Remove the $2E header
        response_bytes = bytes.fromhex(response)  # Convert to bytes
    except ValueError as e:
        _LOGGER.error("Invalid response format: %s", e)
        return "Invalid response format"

    # Parse configuration data
    pc_link_address = response_bytes[:2].hex().upper()  # Extract the PC-LINK address
    module_type = classify_module_type(response_bytes)  # Classify module type
    module_address = extract_impacted_module_address(response_bytes)  # Extract impacted module address
    additional_segment = extract_additional_segment(response_bytes)  # Extract additional flags/control
    channel_data = extract_channel_data(response_bytes)  # Extract channel data
    timer_mode_data = response_bytes[14:15]  # Extract timer and mode data

    timer, mode = decode_timer_and_mode(timer_mode_data)  # Decode timer and mode

    parsed_data.append(f"PC-LINK Address: {pc_link_address}")
    parsed_data.append(f"Module Type: {module_type}")
    parsed_data.append(f"Additional Segment: {additional_segment}")
    parsed_data.append(f"Impacted Module Address: {module_address}")
    parsed_data.append(f"Linked Channels: {', '.join(decode_channels(channel_data))}")
    parsed_data.append(f"Timer: {timer}")
    parsed_data.append(f"Mode: {mode}")

    for line in parsed_data:
        _LOGGER.debug(line)
    return "\n".join(parsed_data)

def extract_impacted_module_address(payload):
    """ Extracts the impacted module address from the payload. OK """
    try:
        module_address = payload[10:12].hex().upper()
        return module_address
    except Exception as e:
        _LOGGER.debug("Failed to extract impacted module address: %s", e)
        return "Unknown"

def classify_module_type(payload):
    """ Determines the type of module based on its payload. OK """ 
    try:
        module_type_bits = payload[2:3].hex().upper()  # Extract 1 byte
        module_types = {
            "03": "Compact Switch Module",  # Compact module
            "04": "Switch Module",          # Standard switch
            "05": "Dimmer Module",          # Dimmer control
            "06": "Shutter Module",         # Shutter control
            "07": "Thermostat Module",      # Thermostat
            "08": "Scene Module",           # Scene configuration module
            "09": "Input Module",           # Input handling
            "0A": "Relay Module",           # Relay for high-power devices
            "0B": "Blind Module",           # Blinds control
            "0C": "PC-Link Interface",      # PC-Link module
            "0D": "Logic Module",           # Logic processor for automation
            "0E": "Timer Module",           # Timer-specific module
            "0F": "Custom Module",          # Reserved for custom or user-defined modules
        }
        module_type = module_types.get(module_type_bits, "Unknown Module")
        return module_type
    except Exception as e:
        _LOGGER.debug("Failed to classify module type: %s", e)
        return "Unknown"

def extract_additional_segment(payload):
    """Extract additional configuration or flags from the payload. OK"""
    try:
        additional_segment = payload[3:10].hex().upper()
        _LOGGER.debug("Extracted Additional Segment: %s", additional_segment)
        return additional_segment
    except Exception as e:
        _LOGGER.debug("Failed to extract additional segment: %s", e)
        return "Unknown"

def decode_channels(channel_data):
    """
    Decode active channels (keys) from the channel data dictionary.
    """
    channels = []
    try:
        # Map each key to its corresponding channel name or ID
        for key, active in channel_data.items():
            if active:  # If the key is active
                channels.append(key)
        
        _LOGGER.debug("Decoded Channels: %s", channels)
        return channels
    except Exception as e:
        _LOGGER.debug("Failed to decode channels from data: %s", e)
        return []

def extract_channel_data(payload):
    """
    Extract channel data from the payload.
    Decodes specific bits for Key A, B, C, and D.
    """
    try:
        # Extracting the byte containing the channel/key information
        key_byte = payload[10]  # Adjust index based on documentation; example uses byte 10.
        
        # Extract individual bits for Key A, B, C, and D
        key_a = (key_byte & 0b00000001) > 0  # LSB
        key_b = (key_byte & 0b00000010) > 0
        key_c = (key_byte & 0b00000100) > 0
        key_d = (key_byte & 0b00001000) > 0  # MSB
        
        # Return a dictionary with decoded key states
        return {
            "Key A": key_a,
            "Key B": key_b,
            "Key C": key_c,
            "Key D": key_d,
        }
    except Exception as e:
        _LOGGER.debug("Failed to extract channel data: %s", e)
        return {
            "Key A": False,
            "Key B": False,
            "Key C": False,
            "Key D": False,
        }

def decode_timer_and_mode(timer_mode_byte):
    """Decode timer and mode from the combined byte."""
    try:
        timer_map = {
            "0": "0 seconds",
            "1": "6 seconds",
            "2": "12 seconds",
            "3": "18 seconds",
            "4": "24 seconds",
            "5": "30 seconds",
            "6": "36 seconds",
            "7": "42 seconds",
            "8": "48 seconds",
            "9": "54 seconds",
            "A": "60 seconds (1 minute)",
            "B": "2 minutes",
            "C": "3 minutes",
            "D": "5 minutes",
            "E": "10 minutes",
            "F": "20 minutes",
        }

        mode_map = {
            "0": "M1 (On/Off)",
            "1": "M2 (Timer-based On)",
            "2": "M3 (Timer-based Off)",
            "3": "M4 (Impulse)",
            "4": "M5 (Delayed Down)",
            "5": "M6 (Delayed Up)",
            "6": "M7 (Short Down)",
            "7": "M8 (Short Up)",
            "8": "M9 (Custom Mode 1)",
            "9": "M10 (Custom Mode 2)",
            "A": "M11 (Reserved)",
            "B": "M12 (Reserved)",
        }

        timer = timer_map.get(timer_mode_byte.hex()[1], "Unknown Timer")  # Lower nibble
        mode = mode_map.get(timer_mode_byte.hex()[0], "Unknown Mode")  # Higher nibble

        return timer, mode
    except Exception as e:
        _LOGGER.debug("Failed to decode timer and mode: %s", e)
        return "Unknown Timer", "Unknown Mode"
