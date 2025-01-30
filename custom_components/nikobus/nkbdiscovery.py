import logging

_LOGGER = logging.getLogger(__name__)

async def parse_response(payload):
    try:
        # Remove '$' prefix and convert payload to bytes
        payload = payload.lstrip("$")
        payload_bytes = bytes.fromhex(payload)

        # Extract the device type from the payload (byte at index 7)
        device_type_hex = format(payload_bytes[7], '02X')
        _LOGGER.debug(f"Extracted device type (hex): {device_type_hex}")

        # Extract the device address from the payload (bytes at index 10 to 12)
        device_address = payload_bytes[10:13].hex().upper()
        _LOGGER.debug(f"Extracted device address: {device_address}")

        # Classify the device
        device_info = classify_device_type(device_type_hex)
        _LOGGER.debug(f"Classified device type: {device_info}")

        if device_info == "Unknown Device":
            _LOGGER.warning(
                f"Unknown device detected: Type {device_type_hex} at Address {device_address}. "
                "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information."
            )
            return

        # Handle Modules
        if device_info["Category"] == "Module":
            _LOGGER.debug(
                f"Discovered Module - Type: {device_info['Name']}, Model: {device_info.get('Model', 'N/A')}, "
                f"Address: {device_address}"
            )

        # Handle Buttons
        elif device_info["Category"] == "Button":
            _LOGGER.debug(
                f"Discovered Button - Type: {device_info['Name']}, Model: {device_info.get('Model', 'N/A')}, "
                f"Address: {device_address}"
            )

    except Exception as e:
        _LOGGER.error(f"Failed to parse Nikobus payload: {e}")

def classify_device_type(device_type_hex):
    """
    Classify the device type based on the device type hex value.
    """
    device_types = {
        # Known Device Types
        "01": {"Category": "Module", "Model": "05-000-02", "Name": "Switch Module"},                            # A5C9, 0747
        "02": {"Category": "Module", "Model": "05-001-02", "Name": "Roller Shutter Module"},                    # 0591, 9483
        "03": {"Category": "Module", "Model": "05-007-02", "Name": "Dimmer Module"},                            # 6C0E
        "04": {"Category": "Button", "Model": "05-342", "Name": "Button with 2 Operation Points"},              # 72EF, FE09, 560C, CCC1, 020C, 4C16, 7E12, D41E, 7C15, 8214, D81E, 4A05, C81E, F8F2, 4C58
        "06": {"Category": "Button", "Model": "05-346", "Name": "Button with 4 Operation Points"},              # B443, 54C5, 121F, 182F, 848D, A61E, A0FB, 4A0D, 4AFE, 40A8, 480D, 9EF3
        "08": {"Category": "Module", "Model": "05-201", "Name": "PC Logic"},                                    # 0C94
        "09": {"Category": "Module", "Model": "05-002-02", "Name": "Compact Switch Module"},                    # 055B
        "0A": {"Category": "Module", "Model": "05-200", "Name": "PC Link"},                                     # F586
        "0C": {"Category": "Button", "Model": "05-348", "Name": "IR Button with 4 Operation Points"},           # 801C, C0FE
        "12": {"Category": "Button", "Model": "05-349", "Name": "Button with 8 Operation Points"},              # 56F2, E0F1
        "1F": {"Category": "Button", "Model": "05-311", "Name": "RF Transmitter with 2 Operation Points"},      # F658
        "23": {"Category": "Button", "Model": "05-312", "Name": "RF Transmitter with 4 Operation Points"},      # 5012, 1549, FFFF
        "25": {"Category": "Button", "Model": "05-055", "Name": "All-Function Interface"},                      # 8723, 6621
        "3F": {"Category": "Button", "Model": "05-344", "Name": "Feedback Button with 2 Operation Points"},     # 4E65
        "40": {"Category": "Button", "Model": "05-347", "Name": "Feedback Button with 4 Operation Points"},     # 2B15, 6936
        "42": {"Category": "Module", "Model": "05-207", "Name": "Feedback Module"},                             # 6C96
        "44": {"Category": "Button", "Model": "05-057", "Name": "Switch Interface"},                            # DC34
    }

    return device_types.get(device_type_hex, "Unknown Device")
