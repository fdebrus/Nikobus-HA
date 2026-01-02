import logging

_LOGGER = logging.getLogger(__name__)


def reverse_hex(hex_str):
    """Reverse the bytes in a hex string and return as upper-case hex."""
    b = bytes.fromhex(hex_str)
    reversed_b = b[::-1]
    return reversed_b.hex().upper()


def classify_device_type(device_type_hex, device_types):
    """Classify the device type from its hex code."""
    return device_types.get(
        device_type_hex,
        {
            "Category": "Unknown",
            "Model": "Unknown",
            "Channels": 0,
            "Name": "Unknown",
        },
    )


def convert_nikobus_address(address_string):
    """Convert a hex address string to a Nikobus address."""
    try:
        address = int(address_string, 16)
        nikobus_address = 0
        for i in range(21):
            nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)
        nikobus_address <<= 1
        button = (address >> 21) & 0x07
        final_address = nikobus_address + button
        return f"{final_address:06X}"
    except ValueError:
        return f"[{address_string}]"


def get_button_address(payload):
    """Convert a payload hex string into a button address."""
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


def get_push_button_address(
    key_raw, button_address, key_mapping_module, coordinator_get_button_channels, convert_func=None
):
    second_part = False
    num_channels = coordinator_get_button_channels(button_address)
    if num_channels is None:
        if button_address.startswith("0"):
            num_channels = 4
        elif int(button_address[-1], 16) % 2 == 1:
            normalized_address = f"{int(button_address, 16) - 1:06X}"
            num_channels = coordinator_get_button_channels(normalized_address)
            if num_channels is not None:
                _LOGGER.info(
                    "Normalized button_address from %s to %s",
                    button_address,
                    normalized_address,
                )
                button_address = normalized_address
                second_part = True
            else:
                _LOGGER.error(
                    "Could not determine channels for button address %s or normalized %s",
                    button_address,
                    normalized_address,
                )
                return None, button_address
        else:
            _LOGGER.error(
                "Could not determine channels for button address %s", button_address
            )
            return None, button_address

    if convert_func is None:
        convert_func = convert_nikobus_address
    push_button_address = convert_func(button_address)

    mapping = key_mapping_module.get(num_channels, {})
    _LOGGER.debug(
        "Debug: key_raw=%s, mapping keys=%s", key_raw, list(mapping.keys())
    )

    effective_key = int(key_raw)
    if num_channels == 8 and second_part:
        effective_key = effective_key + 4

    if effective_key not in mapping:
        _LOGGER.debug(
            "Missing mapping for effective_key '%s'. Available keys: %s",
            effective_key,
            list(mapping.keys()),
        )
        return None, button_address

    add_value = int(mapping[effective_key], 16)
    original_nibble = int(push_button_address[0], 16)
    new_nibble_value = original_nibble + add_value
    new_nibble_hex = f"{new_nibble_value:X}"
    final_push_button_address = new_nibble_hex + push_button_address[1:]

    return final_push_button_address, button_address


def get_timer_value(timer_list, idx, default="Unknown"):
    """Safely fetch timer value by index from mapping, fallback if missing."""
    if timer_list is None:
        return default
    if isinstance(timer_list, list) and len(timer_list) > idx:
        return timer_list[idx]
    if isinstance(timer_list, list) and len(timer_list) > 0:
        return timer_list[-1]
    return default

def decode_command_payload(
    payload_hex,
    module_type,
    key_mapping_module,
    channel_mapping,
    mode_mappings,
    timer_mappings,
    coordinator_get_button_channels,
    convert_func=None,
):
    """Decode the command payload into its fields with detailed debug logging."""
    if not isinstance(payload_hex, str):
        payload_hex = payload_hex.hex().upper()
    payload_hex = payload_hex.upper()

    if len(payload_hex) < 12:
        _LOGGER.error("Payload too short for valid decode: %s", payload_hex)
        return None

    if payload_hex == "FFFFFFFFFFFF":
        _LOGGER.debug("Skipping terminator payload: %s", payload_hex)
        return None

    raw_bytes = [payload_hex[i:i+2] for i in range(0, len(payload_hex), 2)]
    if raw_bytes[:5] == ["FF", "FF", "FF", "FF", "FF"]:
        _LOGGER.debug(
            "Skipping reversed terminator/filler payload: %s | raw_bytes=%s",
            payload_hex,
            raw_bytes,
        )
        return None

    if payload_hex.endswith("FFFFFF"):
        _LOGGER.debug("Skipping payload with invalid button address: %s", payload_hex)
        return None

    try:
        t2_raw = int(payload_hex[1], 16)
        key_raw = int(payload_hex[2], 16)
        channel_raw = int(payload_hex[3], 16)
        t1_raw = int(payload_hex[4], 16)
        mode_raw = int(payload_hex[5], 16)
    except ValueError:
        _LOGGER.error("Invalid command hex: %s", payload_hex)
        return None

    if key_raw == 0xF or channel_raw == 0xF or mode_raw == 0xF:
        _LOGGER.debug(
            "Skipping filler payload due to reserved nibble: key=%s channel=%s mode=%s payload=%s",
            key_raw,
            channel_raw,
            mode_raw,
            payload_hex,
        )
        return None

    # DEBUG: Show full payload bytes and all extracted fields
    _LOGGER.debug(
        "Nikobus DECODE | payload=%s | raw_bytes=%s | t2_raw=%d | key_raw=%d | channel_raw=%d | t1_raw=%d | mode_raw=%d | module_type=%s",
        payload_hex, raw_bytes, t2_raw, key_raw, channel_raw, t1_raw, mode_raw, module_type
    )

    # GET BUTTON ADDRESS
    button_address_hex = payload_hex[-6:]
    button_address = get_button_address(button_address_hex)

    # GET PUSH BUTTON ADDRESS
    push_button_address, button_address = get_push_button_address(
        key_raw, button_address, key_mapping_module, coordinator_get_button_channels, convert_func
    )

    # GET CHANNEL LABEL
    channel_label = channel_mapping.get(
        channel_raw, f"Unknown Channel ({channel_raw})"
    )

    if channel_label.startswith("Unknown Channel"):
        _LOGGER.debug(
            "Mapping fail: channel_raw=%s | channel_keys=%s | module_type=%s | payload=%s",
            channel_raw, list(channel_mapping.keys()), module_type, payload_hex
        )

    # GET MODE AND TIMING
    try:
        mode_mapping = mode_mappings[module_type]
        timer_mapping = timer_mappings[module_type]
    except KeyError:
        _LOGGER.error("Unknown module_type '%s'. Available: %s", module_type, list(mode_mappings.keys()))
        return None

    mode_label = mode_mapping.get(mode_raw, f"Unknown Mode ({mode_raw})")
    if mode_label.startswith("Unknown Mode"):
        _LOGGER.debug(
            "Unknown mode: mode_raw=%d | mode_keys=%s | module_type=%s | payload=%s",
            mode_raw, list(mode_mapping.keys()), module_type, payload_hex
        )

    t1_val = None
    t2_val = None

    if module_type == "switch_module":
        if mode_raw in [5, 6]:
            t1_val = get_timer_value(timer_mapping.get(t1_raw, ["Unknown"]), 0)
        elif mode_raw in [8, 9]:
            t1_val = get_timer_value(timer_mapping.get(t1_raw, ["Unknown"]), 1)
        elif mode_raw in [1, 2]:
            t1_val = get_timer_value(timer_mapping.get(t1_raw, ["Unknown"]), 2)
    elif module_type == "dimmer_module":
        if mode_raw in [0, 1, 2, 10, 11]:
            t1_val = get_timer_value(timer_mapping.get(t1_raw, ["Unknown"]), 1)
            t2_val = get_timer_value(timer_mapping.get(t2_raw, ["Unknown"]), 2)
        elif mode_raw == 3:
            t2_val = get_timer_value(timer_mapping.get(t2_raw, ["Unknown"]), 2)
        elif mode_raw in [4, 5, 6, 7, 8, 9]:
            t1_val = get_timer_value(timer_mapping.get(t1_raw, ["Unknown"]), 0)
            t2_val = get_timer_value(timer_mapping.get(t2_raw, ["Unknown"]), 2)
    elif module_type == "roller_module":
        t1_val = get_timer_value(timer_mapping.get(t1_raw, ["Unknown"]), 0)

    # Final debug log for full decoded output
    _LOGGER.debug(
        "Decoded payload: K=%s, C=%s, T1=%s, T2=%s, M=%s, button_address=%s, push_button_address=%s",
        key_raw, channel_label, t1_val, t2_val, mode_label, button_address, push_button_address
    )

    return {
        "payload": payload_hex,
        "button_address": button_address,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": t2_raw,
        "K": f"{key_raw}",
        "C": f"{channel_label}",
        "T1": f"{t1_val}",
        "T2": f"{t2_val}",
        "M": f"{mode_label}",
    }