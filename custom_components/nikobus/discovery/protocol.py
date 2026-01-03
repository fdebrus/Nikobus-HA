import logging

_LOGGER = logging.getLogger(__name__)


_DIMMER_CANDIDATE_SUCCESS: dict[str, int] = {}


def _looks_like_prefixed_dimmer_frame(raw_bytes: bytes) -> bool:
    """Return True if the bytes match a known prefixed dimmer opcode layout."""

    if len(raw_bytes) < 2:
        return False

    opcode = raw_bytes[1]
    # Dimmer discovery/command frames have been observed with an extra leading
    # prefix byte (often 0xFF) before the opcode. Known opcodes live in the
    # lower ranges, so filter by that to avoid stripping for unrelated frames.
    return opcode in {0x08, 0x0B, 0x0C, 0x0D}


def normalize_payload(payload_hex: str, module_type: str) -> tuple[bytes | None, bytes | None]:
    """Normalize payload alignment and strip protocol prefixes when needed.

    The Nikobus dimmer frames may include a leading prefix byte (commonly 0xFF)
    that is not part of the logical command, which would shift subsequent nibble
    decoding and make fields like key/channel invalid. We normalize by working
    on bytes first, optionally trimming those prefix bytes, and returning a
    consistent payload layout for downstream parsing.
    """

    try:
        original_bytes = bytes.fromhex(payload_hex)
    except ValueError:
        _LOGGER.error("Invalid payload hex: %s", payload_hex)
        return None, None

    normalized_bytes = original_bytes

    if original_bytes and original_bytes[0] == 0xFF:
        if module_type == "dimmer_module" or _looks_like_prefixed_dimmer_frame(original_bytes):
            idx = 0
            while idx < len(original_bytes) and original_bytes[idx] == 0xFF:
                idx += 1
            normalized_bytes = original_bytes[idx:]
            _LOGGER.debug(
                "Stripped %s prefix byte(s) from payload | raw=%s normalized=%s module_type=%s",
                idx,
                payload_hex,
                normalized_bytes.hex().upper(),
                module_type,
            )

    return normalized_bytes, original_bytes


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


def _validate_decoded_result(
    decoded,
    module_type,
    key_mapping_module,
    channel_mapping,
    coordinator_get_button_channels,
    raw_payload_hex,
    normalized_payload_hex,
):
    """Validate decoded payload fields before using them to update state."""

    if decoded is None:
        return None

    key_raw = decoded.get("key_raw")
    channel_raw = decoded.get("channel_raw")
    mode_raw = decoded.get("mode_raw")
    channel_count = decoded.get("channel_count")
    button_channel_count = decoded.get("button_channel_count")

    known_keys = {k for mapping in key_mapping_module.values() for k in mapping}
    expected_keys = known_keys
    if (
        button_channel_count is not None
        and button_channel_count in key_mapping_module
    ):
        expected_keys = set(key_mapping_module[button_channel_count].keys())

    invalid = False

    if expected_keys and key_raw not in expected_keys and key_raw not in known_keys:
        invalid = True

    if channel_count is not None:
        if channel_raw is None or channel_raw < 0 or channel_raw >= channel_count:
            invalid = True
    elif channel_mapping and channel_raw not in channel_mapping:
        invalid = True

    if invalid:
        _LOGGER.warning(
            "Skipping payload after validation failure | module_type=%s raw_payload=%s normalized_payload=%s key=%s channel=%s channel_count=%s button_channel_count=%s mode=%s",
            module_type,
            raw_payload_hex,
            normalized_payload_hex,
            key_raw,
            channel_raw,
            channel_count,
            button_channel_count,
            mode_raw,
        )
        return None

    _LOGGER.debug(
        "Validation passed | module_type=%s raw_payload=%s normalized_payload=%s key=%s channel=%s channel_mask=%s channel_count=%s button_channel_count=%s",
        module_type,
        raw_payload_hex,
        normalized_payload_hex,
        key_raw,
        channel_raw,
        decoded.get("channel_mask"),
        channel_count,
        button_channel_count,
    )

    return decoded

def _nibble_high(raw_bytes, idx):
    try:
        return int(raw_bytes[idx][0], 16)
    except (IndexError, ValueError, TypeError):
        return None


def _nibble_low(raw_bytes, idx):
    try:
        return int(raw_bytes[idx][1], 16)
    except (IndexError, ValueError, TypeError):
        return None


def _channel_index_from_mask(
    channel_raw: int | None,
    channel_mapping: dict[int, str],
    module_type: str | None = None,
) -> tuple[int | None, int | None]:
    """Normalize channel from possible bitmask to zero-based index.

    For switch and roller modules the discovery payload already uses direct
    channel indexes, so the value can be used as-is when it exists in the
    mapping. Other module types keep the legacy bitmask interpretation.
    """

    if channel_raw is None:
        return None, None

    if module_type in {"switch_module", "roller_module"}:
        if channel_raw in channel_mapping:
            return channel_raw, None
        return None, channel_raw

    if channel_raw > 0 and channel_raw & (channel_raw - 1) == 0:
        return int(channel_raw.bit_length() - 1), channel_raw

    if channel_raw in channel_mapping:
        return channel_raw, channel_raw

    return None, channel_raw


def _calculate_timer_values(module_type, mode_raw, t1_raw, t2_raw, timer_mapping):
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

    return t1_val, t2_val


def _decode_switch_or_roller(
    payload_hex,
    module_type,
    key_mapping_module,
    channel_mapping,
    mode_mappings,
    timer_mappings,
    coordinator_get_button_channels,
    convert_func,
    raw_bytes,
    *,
    logical_channel_count: int | None = None,
):
    # Normalized layout (nibbles):
    #   byte0 -> [ignored_hi, T2]
    #   byte1 -> [Key, Channel]
    #   byte2 -> [T1, Mode]
    #   bytes3-5 -> Button address
    t2_raw = _nibble_low(raw_bytes, 0)
    key_raw = _nibble_high(raw_bytes, 1)
    channel_candidates = [
        ("byte1_low", _nibble_low(raw_bytes, 1)),
        ("byte2_low", _nibble_low(raw_bytes, 2)),
    ]
    t1_raw = _nibble_high(raw_bytes, 2)
    mode_raw = _nibble_low(raw_bytes, 2)

    if None in (t2_raw, key_raw, t1_raw, mode_raw):
        _LOGGER.error("Invalid command bytes: %s", raw_bytes)
        return None

    channel_reserved = channel_candidates[0][1]

    if key_raw == 0xF or channel_reserved == 0xF or mode_raw == 0xF:
        _LOGGER.debug(
            "Skipping filler payload due to reserved nibble: key=%s channel=%s mode=%s payload=%s",
            key_raw,
            channel_reserved,
            mode_raw,
            payload_hex,
        )
        return None

    button_address_hex = payload_hex[-6:]
    button_address = get_button_address(button_address_hex)

    push_button_address, button_address = get_push_button_address(
        key_raw, button_address, key_mapping_module, coordinator_get_button_channels, convert_func
    )

    button_channel_count = coordinator_get_button_channels(button_address)
    channel_count = logical_channel_count
    if channel_count is None:
        channel_count = {"switch_module": 12, "roller_module": 6}.get(module_type)
        logical_channel_count = channel_count

    _LOGGER.debug(
        "Channel count comparison | logical_channel_count=%s button_channel_count=%s",
        logical_channel_count,
        button_channel_count,
    )

    channel_raw = None
    channel_mask = None
    channel_source = None
    for source, candidate in channel_candidates:
        idx, mask = _channel_index_from_mask(candidate, channel_mapping, module_type)
        if idx is None:
            continue
        if channel_count is None or idx < channel_count:
            channel_raw = idx
            channel_mask = mask
            channel_source = source
            break

    if channel_raw is None:
        channel_raw, channel_mask = _channel_index_from_mask(
            channel_candidates[0][1], channel_mapping, module_type
        )
        channel_source = channel_candidates[0][0]

    channel_label = channel_mapping.get(channel_raw, f"Unknown Channel ({channel_raw})")

    if channel_label.startswith("Unknown Channel"):
        _LOGGER.debug(
            "Mapping fail: channel_raw=%s | channel_keys=%s | module_type=%s | payload=%s",
            channel_raw,
            list(channel_mapping.keys()),
            module_type,
            payload_hex,
        )

    try:
        mode_mapping = mode_mappings[module_type]
        timer_mapping = timer_mappings[module_type]
    except KeyError:
        _LOGGER.error(
            "Unknown module_type '%s'. Available: %s", module_type, list(mode_mappings.keys())
        )
        return None

    mode_label = mode_mapping.get(mode_raw, f"Unknown Mode ({mode_raw})")
    if mode_label.startswith("Unknown Mode"):
        _LOGGER.debug(
            "Unknown mode: mode_raw=%d | mode_keys=%s | module_type=%s | payload=%s",
            mode_raw,
            list(mode_mapping.keys()),
            module_type,
            payload_hex,
        )

    t1_val, t2_val = _calculate_timer_values(
        module_type, mode_raw, t1_raw, t2_raw, timer_mapping
    )

    _LOGGER.debug(
        "Channel extraction | raw_chunk=%s reversed_payload=%s candidates=%s selected=%s mask=%s channel_count=%s button_channel_count=%s",
        payload_hex,
        raw_bytes,
        channel_candidates,
        channel_raw,
        channel_mask,
        channel_count,
        button_channel_count,
    )

    _LOGGER.debug(
        "Decoded payload: K=%s, C=%s, T1=%s, T2=%s, M=%s, button_address=%s, push_button_address=%s",
        key_raw,
        channel_label,
        t1_val,
        t2_val,
        mode_label,
        button_address,
        push_button_address,
    )

    return {
        "payload": payload_hex,
        "button_address": button_address,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "channel_mask": channel_mask,
        "channel_source": channel_source,
        "channel_count": channel_count,
        "button_channel_count": button_channel_count,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": t2_raw,
        "K": f"{key_raw}",
        "C": f"{channel_label}",
        "T1": t1_val,
        "T2": t2_val,
        "M": f"{mode_label}",
    }


def _build_dimmer_candidates(
    raw_bytes,
    channel_mapping,
    key_mapping_module,
    mode_mapping,
    num_channels,
):
    key_options = [
        ("b3_hi", _nibble_high(raw_bytes, 3)),
        ("b3_lo", _nibble_low(raw_bytes, 3)),
        ("b2_hi", _nibble_high(raw_bytes, 2)),
        ("b2_lo", _nibble_low(raw_bytes, 2)),
    ]
    channel_options = [
        ("b3_lo", _nibble_low(raw_bytes, 3)),
        ("b3_hi", _nibble_high(raw_bytes, 3)),
        ("b2_lo", _nibble_low(raw_bytes, 2)),
        ("b2_hi", _nibble_high(raw_bytes, 2)),
    ]
    mode_options = [
        ("b4_lo", _nibble_low(raw_bytes, 4)),
        ("b2_lo", _nibble_low(raw_bytes, 2)),
        ("b3_lo", _nibble_low(raw_bytes, 3)),
    ]
    t2_options = [
        ("b0_hi", _nibble_high(raw_bytes, 0)),
        ("b2_hi", _nibble_high(raw_bytes, 2)),
    ]

    possible_keys = set()
    if num_channels is not None:
        possible_keys.update(key_mapping_module.get(num_channels, {}).keys())
    else:
        for mapping in key_mapping_module.values():
            possible_keys.update(mapping.keys())

    candidates = []
    for key_label, key_val in key_options:
        if key_val is None:
            continue
        for channel_label, channel_val in channel_options:
            if channel_val is None:
                continue
            for mode_label, mode_val in mode_options:
                if mode_val is None:
                    continue
                t1_val = None
                if mode_label == "b4_lo":
                    t1_val = _nibble_high(raw_bytes, 4)
                elif mode_label == "b2_lo":
                    t1_val = _nibble_high(raw_bytes, 2)
                elif mode_label == "b3_lo":
                    t1_val = _nibble_high(raw_bytes, 3)
                for t2_label, t2_val in t2_options:
                    if t2_val is None:
                        continue
                    candidate_id = f"k:{key_label}|c:{channel_label}|m:{mode_label}|t2:{t2_label}"
                    valid_key = key_val in possible_keys
                    valid_channel = channel_val in channel_mapping
                    valid_mode = mode_val in mode_mapping
                    candidates.append(
                        {
                            "id": candidate_id,
                            "key_raw": key_val,
                            "channel_raw": channel_val,
                            "mode_raw": mode_val,
                            "t1_raw": t1_val,
                            "t2_raw": t2_val,
                            "valid_key": valid_key,
                            "valid_channel": valid_channel,
                            "valid_mode": valid_mode,
                            "valid_all": all([valid_key, valid_channel, valid_mode]),
                            "count": _DIMMER_CANDIDATE_SUCCESS.get(candidate_id, 0),
                        }
                    )

    return candidates


def _decode_dimmer(
    payload_hex,
    key_mapping_module,
    channel_mapping,
    mode_mappings,
    timer_mappings,
    coordinator_get_button_channels,
    convert_func,
    raw_bytes,
):
    if len(raw_bytes) < 5:
        _LOGGER.error("Dimmer payload has unexpected length: %s", payload_hex)
        return None

    button_address_hex = payload_hex[-6:]
    button_address = get_button_address(button_address_hex)
    num_channels = coordinator_get_button_channels(button_address)

    try:
        mode_mapping = mode_mappings["dimmer_module"]
        timer_mapping = timer_mappings["dimmer_module"]
    except KeyError:
        _LOGGER.error(
            "Unknown module_type 'dimmer_module'. Available: %s", list(mode_mappings.keys())
        )
        return None

    opcode = raw_bytes[0] if raw_bytes else None
    candidates = _build_dimmer_candidates(
        raw_bytes, channel_mapping, key_mapping_module, mode_mapping, num_channels
    )

    _LOGGER.debug(
        "Dimmer decode candidates | payload=%s | raw_bytes=%s | opcode=%s | candidates=%s",
        payload_hex,
        raw_bytes,
        opcode,
        candidates,
    )

    passing = [c for c in candidates if c["valid_all"]]
    if not passing:
        passing = sorted(
            candidates,
            key=lambda c: (int(c["valid_key"]), int(c["valid_channel"]), int(c["valid_mode"])),
            reverse=True,
        )[:1]

    if not passing:
        _LOGGER.error("No valid candidate interpretations for dimmer payload: %s", payload_hex)
        return None

    selected = max(passing, key=lambda c: c.get("count", 0))
    _DIMMER_CANDIDATE_SUCCESS[selected["id"]] = selected.get("count", 0) + 1

    key_raw = selected["key_raw"]
    channel_raw = selected["channel_raw"]
    mode_raw = selected["mode_raw"]
    t1_raw = selected["t1_raw"]
    t2_raw = selected["t2_raw"]

    if key_raw in (0xF, None) or channel_raw in (0xF, None) or mode_raw in (0xF, None):
        _LOGGER.debug(
            "Skipping dimmer payload due to invalid candidate values: key=%s channel=%s mode=%s payload=%s",
            key_raw,
            channel_raw,
            mode_raw,
            payload_hex,
        )
        return None

    push_button_address, button_address = get_push_button_address(
        key_raw, button_address, key_mapping_module, coordinator_get_button_channels, convert_func
    )

    channel_label = channel_mapping.get(channel_raw, f"Unknown Channel ({channel_raw})")
    if channel_label.startswith("Unknown Channel"):
        _LOGGER.debug(
            "Mapping fail: channel_raw=%s | channel_keys=%s | module_type=dimmer_module | payload=%s",
            channel_raw,
            list(channel_mapping.keys()),
            payload_hex,
        )

    mode_label = mode_mapping.get(mode_raw, f"Unknown Mode ({mode_raw})")
    if mode_label.startswith("Unknown Mode"):
        _LOGGER.debug(
            "Unknown mode: mode_raw=%d | mode_keys=%s | module_type=dimmer_module | payload=%s",
            mode_raw,
            list(mode_mapping.keys()),
            payload_hex,
        )

    t1_val, t2_val = _calculate_timer_values(
        "dimmer_module", mode_raw, t1_raw, t2_raw, timer_mapping
    )

    _LOGGER.debug(
        "Selected dimmer candidate %s | key=%s channel=%s mode=%s t1=%s t2=%s",
        selected.get("id"),
        key_raw,
        channel_raw,
        mode_raw,
        t1_val,
        t2_val,
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
        "T1": t1_val,
        "T2": t2_val,
        "M": f"{mode_label}",
    }


def decode_command_payload(
    payload_hex,
    module_type,
    key_mapping_module,
    channel_mapping,
    mode_mappings,
    timer_mappings,
    coordinator_get_button_channels,
    convert_func=None,
    logical_channel_count: int | None = None,
    *,
    reverse_before_decode: bool = False,
    raw_chunk_hex: str | None = None,
):
    """Decode the command payload into its fields with detailed debug logging."""
    if not isinstance(payload_hex, str):
        payload_hex = payload_hex.hex().upper()
    payload_hex = payload_hex.upper()
    input_payload_hex = payload_hex

    reversed_payload_hex = None
    if reverse_before_decode:
        reversed_payload_hex = reverse_hex(payload_hex)
        payload_hex = reversed_payload_hex

    _LOGGER.debug(
        "Discovery decode input | module_type=%s raw_chunk=%s reversed_chunk=%s",
        module_type,
        (raw_chunk_hex or input_payload_hex),
        reversed_payload_hex or input_payload_hex,
    )

    normalized_bytes, original_bytes = normalize_payload(payload_hex, module_type)
    if normalized_bytes is None:
        return None

    payload_hex = normalized_bytes.hex().upper()
    if len(payload_hex) < 10:
        _LOGGER.error(
            "Payload too short for valid decode after normalization: raw=%s normalized=%s",
            original_bytes.hex().upper() if original_bytes else payload_hex,
            payload_hex,
        )
        return None

    if payload_hex == "FFFFFFFFFFFF":
        _LOGGER.debug("Skipping terminator payload: %s", payload_hex)
        return None

    raw_bytes = [f"{b:02X}" for b in normalized_bytes]
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

    raw_payload_hex = (raw_chunk_hex or input_payload_hex).upper()

    decoded = None
    if module_type in {"switch_module", "roller_module"}:
        decoded = _decode_switch_or_roller(
            payload_hex,
            module_type,
            key_mapping_module,
            channel_mapping,
            mode_mappings,
            timer_mappings,
            coordinator_get_button_channels,
            convert_func,
            raw_bytes,
            logical_channel_count=logical_channel_count,
        )

    elif module_type == "dimmer_module":
        decoded = _decode_dimmer(
            payload_hex,
            key_mapping_module,
            channel_mapping,
            mode_mappings,
            timer_mappings,
            coordinator_get_button_channels,
            convert_func,
            raw_bytes,
        )
    else:
        _LOGGER.error(
            "Unknown module_type '%s'. Available: %s", module_type, list(mode_mappings.keys())
        )
        return None

    return _validate_decoded_result(
        decoded,
        module_type,
        key_mapping_module,
        channel_mapping,
        coordinator_get_button_channels,
        raw_payload_hex,
        payload_hex,
    )
