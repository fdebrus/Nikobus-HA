import logging
from typing import Any

from .mapping import CHANNEL_MAPPING

_LOGGER = logging.getLogger(__name__)


_DIMMER_CANDIDATE_SUCCESS: dict[str, int] = {}


def _module_channel_count_or_default(
    module_address: str | None,
    module_type: str | None,
    logical_channel_count: int | None,
    coordinator_get_module_channels,
) -> int | None:
    fallback_default = {
        "switch_module": 12,
        "roller_module": 6,
        "dimmer_module": 12,
    }.get(module_type)

    module_channel_count = None

    if coordinator_get_module_channels and module_address:
        try:
            module_channel_count = coordinator_get_module_channels(module_address)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug(
                "Error retrieving module channel count | module_address=%s error=%s",
                module_address,
                err,
            )

    if isinstance(module_channel_count, int) and module_channel_count > 0:
        return module_channel_count

    if isinstance(logical_channel_count, int) and logical_channel_count > 0:
        return logical_channel_count

    return fallback_default


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


def _normalize_key_index(key_raw, num_channels, key_mapping_module):
    """Normalize raw nibble-based key values into mapping indexes.

    Roller modules report the key nibble (e.g. 0x8/0x4/0xD) instead of the
    zero-based index expected by :data:`KEY_MAPPING_MODULE`. This helper keeps
    valid indexes as-is, otherwise it builds an inverse lookup of the mapping
    values and resolves the nibble to the proper index. If the button channel
    count is unknown we try all mappings and only accept an unambiguous match.
    """

    if key_raw is None:
        return None, num_channels, []

    inverse_mappings = {
        channels: {value.upper(): index for index, value in mapping.items()}
        for channels, mapping in key_mapping_module.items()
    }

    # Already a valid index for the detected channel count.
    if num_channels in key_mapping_module and key_raw in key_mapping_module[num_channels]:
        return key_raw, num_channels, [(num_channels, key_raw, "index")]

    nibble_hex = f"{int(key_raw):X}".upper()

    # Channel-count-specific inverse lookup first.
    if num_channels in inverse_mappings and nibble_hex in inverse_mappings[num_channels]:
        normalized_key = inverse_mappings[num_channels][nibble_hex]
        return normalized_key, num_channels, [(num_channels, normalized_key, "inverse")]

    # Fallback: try all mappings, but only accept when unambiguous.
    matches = [
        (channels, inverse_mappings[channels][nibble_hex], "inverse")
        for channels in inverse_mappings
        if nibble_hex in inverse_mappings[channels]
    ]

    if len(matches) == 1:
        return matches[0][1], matches[0][0], matches

    return key_raw, num_channels, matches


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
    module_address: str | None = None,
):
    """Validate decoded payload fields before using them to update state."""

    if decoded is None:
        return None

    key_raw = decoded.get("key_raw")
    channel_raw = decoded.get("channel_raw")
    mode_raw = decoded.get("mode_raw")
    channel_count = decoded.get("channel_count")
    button_channel_count = decoded.get("button_channel_count")

    filler_values = {0xF}
    if module_type == "roller_module":
        filler_values = {0xE, 0xF}

    if key_raw in filler_values or channel_raw in filler_values or mode_raw in filler_values:
        _LOGGER.debug(
            "Skipping payload after filler check | module_type=%s module_address=%s key=%s channel=%s mode=%s payload=%s",
            module_type,
            module_address,
            key_raw,
            channel_raw,
            mode_raw,
            normalized_payload_hex,
        )
        return None

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
            _LOGGER.debug(
                "Channel validation failed | module_type=%s module_address=%s extracted_channel=%s module_channel_count=%s",
                module_type,
                module_address,
                channel_raw,
                channel_count,
            )
            invalid = True
    elif channel_mapping and channel_raw not in channel_mapping:
        invalid = True

    if invalid:
        _LOGGER.debug(
            "Skipping payload after validation failure | module_type=%s module_address=%s raw_payload=%s normalized_payload=%s key=%s channel=%s module_channel_count=%s button_channel_count=%s mode=%s",
            module_type,
            module_address,
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
        "Validation passed | module_type=%s module_address=%s raw_payload=%s normalized_payload=%s key=%s channel=%s channel_mask=%s module_channel_count=%s button_channel_count=%s",
        module_type,
        module_address,
        raw_payload_hex,
        normalized_payload_hex,
        key_raw,
        channel_raw,
        decoded.get("channel_mask"),
        channel_count,
        button_channel_count,
    )

    return decoded


def _byte_value(raw_bytes, idx):
    try:
        return int(raw_bytes[idx], 16)
    except (IndexError, ValueError, TypeError):
        return None

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


def _normalize_roller_key(
    raw_nibble: int | None,
    button_channel_count: int | None,
    button_address: str,
) -> tuple[int | None, str]:
    """Normalize roller key nibble into the 0..3 effective key space."""

    if raw_nibble is None:
        return None, "missing"

    if button_channel_count is None:
        return raw_nibble & 0x3, "bitmask_no_channel"

    if button_channel_count == 4:
        if 0 <= raw_nibble < 4:
            return raw_nibble, "direct"
        return raw_nibble % 4, "modulo"

    if 0 <= raw_nibble < button_channel_count:
        return raw_nibble, "direct"

    return raw_nibble & 0x3, "bitmask_fallback"


def _decode_switch_fixed(
    payload_hex,
    key_mapping_module,
    channel_mapping,
    mode_mappings,
    timer_mappings,
    coordinator_get_button_channels,
    coordinator_get_module_channels,
    convert_func,
    raw_bytes,
    *,
    logical_channel_count: int | None = None,
    module_address: str | None = None,
):
    if len(raw_bytes) != 6:
        _LOGGER.debug(
            "Skipping switch payload due to invalid length | module=%s length=%s payload=%s",
            module_address,
            len(raw_bytes),
            payload_hex,
        )
        return None
    # Normalized layout (nibbles):
    #   byte0 -> [ignored_hi, T2]
    #   byte1 -> [Key, Channel]
    #   byte2 -> [T1, Mode]
    #   bytes3-5 -> Button address
    t2_raw = _nibble_low(raw_bytes, 0)
    key_raw = _nibble_high(raw_bytes, 1)
    channel_raw = _nibble_low(raw_bytes, 1)
    t1_raw = _nibble_high(raw_bytes, 2)
    mode_raw = _nibble_low(raw_bytes, 2)

    if None in (t2_raw, key_raw, t1_raw, mode_raw):
        _LOGGER.error("Invalid command bytes: %s", raw_bytes)
        return None

    if key_raw == 0xF or channel_raw == 0xF or mode_raw == 0xF:
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
    button_channel_count = coordinator_get_button_channels(button_address)

    key_raw_nibble = key_raw
    key_raw, _, key_matches = _normalize_key_index(
        key_raw, button_channel_count, key_mapping_module
    )
    _LOGGER.debug(
        "Key normalization | module_type=switch_module raw_nibble=%s normalized=%s button_channels=%s matches=%s",
        key_raw_nibble,
        key_raw,
        button_channel_count,
        key_matches,
    )

    push_button_address, button_address = get_push_button_address(
        key_raw, button_address, key_mapping_module, coordinator_get_button_channels, convert_func
    )

    button_channel_count = coordinator_get_button_channels(button_address)
    channel_count = _module_channel_count_or_default(
        module_address, "switch_module", logical_channel_count, coordinator_get_module_channels
    )

    _LOGGER.debug(
        "Channel count comparison | module_address=%s module_channel_count=%s button_channel_count=%s",
        module_address,
        channel_count,
        button_channel_count,
    )

    channel_raw, channel_mask = _channel_index_from_mask(
        channel_raw, channel_mapping, "switch_module"
    )
    channel_source = "byte1_low"

    if channel_raw is None:
        _LOGGER.debug(
            "Skipping switch payload due to unmapped channel | module=%s payload=%s",
            module_address,
            payload_hex,
        )
        return None

    if channel_count is not None and (
        channel_raw is None or channel_raw < 0 or channel_raw >= channel_count
    ):
        _LOGGER.debug(
            "Skipping switch payload due to out-of-range channel | module=%s channel=%s channel_count=%s payload=%s",
            module_address,
            channel_raw,
            channel_count,
            payload_hex,
        )
        return None

    channel_label = channel_mapping.get(channel_raw, f"Unknown Channel ({channel_raw})")

    if channel_label.startswith("Unknown Channel"):
        _LOGGER.debug(
            "Mapping fail: channel_raw=%s | channel_keys=%s | module_type=%s | payload=%s",
            channel_raw,
            list(channel_mapping.keys()),
            "switch_module",
            payload_hex,
        )

    try:
        mode_mapping = mode_mappings["switch_module"]
        timer_mapping = timer_mappings["switch_module"]
    except KeyError:
        _LOGGER.error(
            "Unknown module_type 'switch_module'. Available: %s", list(mode_mappings.keys())
        )
        return None

    if mode_raw not in mode_mapping:
        _LOGGER.debug(
            "Skipping switch payload due to unknown mode | mode=%s payload=%s",
            mode_raw,
            payload_hex,
        )
        return None

    mode_label = mode_mapping.get(mode_raw, f"Unknown Mode ({mode_raw})")

    t1_val, t2_val = _calculate_timer_values(
        "switch_module", mode_raw, t1_raw, t2_raw, timer_mapping
    )

    _LOGGER.debug(
        "Decoded | type=switch module=%s key=%s channel=%s mode=%s t1=%s t2=%s button=%s payload=%s",
        module_address,
        key_raw,
        channel_raw,
        mode_raw,
        t1_val,
        t2_val,
        push_button_address,
        payload_hex,
    )

    return {
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


def _decode_roller_fixed(
    payload_hex,
    key_mapping_module,
    channel_mapping,
    mode_mappings,
    timer_mappings,
    coordinator_get_button_channels,
    coordinator_get_module_channels,
    convert_func,
    raw_bytes,
    *,
    logical_channel_count: int | None = None,
    module_address: str | None = None,
):
    if len(raw_bytes) != 6:
        _LOGGER.debug(
            "Skipping roller payload due to invalid length | module=%s length=%s payload=%s",
            module_address,
            len(raw_bytes),
            payload_hex,
        )
        return None

    t2_raw = _nibble_low(raw_bytes, 0)
    key_raw_nibble = _nibble_high(raw_bytes, 1)
    selector_byte = _byte_value(raw_bytes, 1)
    t1_raw = _nibble_high(raw_bytes, 2)
    mode_raw = _nibble_low(raw_bytes, 2)

    if None in (t2_raw, key_raw_nibble, selector_byte, t1_raw, mode_raw):
        _LOGGER.debug("Skipping roller payload due to missing nibble | payload=%s", payload_hex)
        return None

    button_address_hex = payload_hex[-6:]
    button_address = get_button_address(button_address_hex)
    button_channel_count = coordinator_get_button_channels(button_address)

    key_raw, method = _normalize_roller_key(
        key_raw_nibble, button_channel_count, button_address
    )
    if key_raw is None:
        _LOGGER.debug("Skipping roller payload due to invalid key | payload=%s", payload_hex)
        return None

    push_button_address, button_address = get_push_button_address(
        key_raw, button_address, key_mapping_module, coordinator_get_button_channels, convert_func
    )

    mode_mapping = mode_mappings.get("roller_module", {})
    if mode_raw not in mode_mapping:
        _LOGGER.debug(
            "Skipping roller payload due to unknown mode | mode=%s payload=%s",
            mode_raw,
            payload_hex,
        )
        return None

    channel_count = _module_channel_count_or_default(
        module_address, "roller_module", logical_channel_count, coordinator_get_module_channels
    )

    selector = selector_byte & 0x0F
    if selector % 2 == 1:
        _LOGGER.debug(
            "Skipping roller payload due to odd selector | selector=0x%X payload=%s",
            selector,
            payload_hex,
        )
        return None

    channel_decoded = (selector // 2) + 1
    if channel_count is not None and not (0 <= channel_decoded < channel_count):
        _LOGGER.debug(
            "Skipping roller payload due to out-of-range channel | selector=0x%X channel=%s module_channel_count=%s payload=%s",
            selector,
            channel_decoded,
            channel_count,
            payload_hex,
        )
        return None

    channel_raw, channel_mask = _channel_index_from_mask(
        channel_decoded, channel_mapping, "roller_module"
    )

    if channel_raw is None:
        _LOGGER.debug(
            "Skipping roller payload due to unmapped channel | selector=0x%X decoded=%s payload=%s",
            selector,
            channel_decoded,
            payload_hex,
        )
        return None

    try:
        timer_mapping = timer_mappings["roller_module"]
    except KeyError:
        _LOGGER.error(
            "Unknown module_type 'roller_module'. Available: %s", list(mode_mappings.keys())
        )
        return None

    t1_val, t2_val = _calculate_timer_values(
        "roller_module", mode_raw, t1_raw, t2_raw, timer_mapping
    )

    _LOGGER.debug(
        "Decoded | type=roller module=%s key=%s channel=%s mode=%s t1=%s t2=%s button=%s payload=%s",
        module_address,
        key_raw,
        channel_raw,
        mode_raw,
        t1_val,
        t2_val,
        push_button_address,
        payload_hex,
    )

    return {
        "payload": payload_hex,
        "button_address": button_address,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "key_raw_nibble": key_raw_nibble,
        "channel_raw": channel_raw,
        "channel_mask": channel_mask,
        "channel_source": "byte1_low",
        "channel_count": channel_count,
        "button_channel_count": button_channel_count,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": t2_raw,
        "K": f"{key_raw}",
        "C": f"{channel_mapping.get(channel_raw, f'Unknown Channel ({channel_raw})')}",
        "T1": t1_val,
        "T2": t2_val,
        "M": f"{mode_mapping.get(mode_raw)}",
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


def _decode_dimmer_fixed(
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
    coordinator_get_module_channels=None,
    logical_channel_count: int | None = None,
    *,
    module_address: str | None = None,
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
    if len(normalized_bytes) != 6:
        _LOGGER.debug(
            "Skipping payload due to invalid length after normalization | raw=%s normalized=%s length=%s",
            original_bytes.hex().upper() if original_bytes else payload_hex,
            payload_hex,
            len(normalized_bytes),
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
    if module_type == "switch_module":
        decoded = _decode_switch_fixed(
            payload_hex,
            key_mapping_module,
            channel_mapping,
            mode_mappings,
            timer_mappings,
            coordinator_get_button_channels,
            coordinator_get_module_channels,
            convert_func,
            raw_bytes,
            logical_channel_count=logical_channel_count,
            module_address=module_address,
        )
    elif module_type == "roller_module":
        decoded = _decode_roller_fixed(
            payload_hex,
            key_mapping_module,
            channel_mapping,
            mode_mappings,
            timer_mappings,
            coordinator_get_button_channels,
            coordinator_get_module_channels,
            convert_func,
            raw_bytes,
            logical_channel_count=logical_channel_count,
            module_address=module_address,
        )
    elif module_type == "dimmer_module":
        decoded = _decode_dimmer_fixed(
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
        module_address,
    )
