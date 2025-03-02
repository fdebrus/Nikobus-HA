"""Nikobus Protocol Utilities."""


def int_to_hex(value: int, digits: int) -> str:
    """Convert an integer to a hexadecimal string with a specified number of digits."""
    return ("00000000" + format(value, "x").upper())[-digits:]


def calc_crc1(data: str) -> int:
    """Calculate CRC-16/ANSI X3.28 (CRC-16-IBM) for the given data."""
    crc = 0xFFFF
    for j in range(len(data) // 2):
        crc ^= int(data[j * 2 : (j + 1) * 2], 16) << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if (crc >> 15) & 1 else crc << 1
    return crc & 0xFFFF

def calc_crc1_ack(data: str) -> int:
    crc = 0x0000
    # Process every two hex digits (one byte)
    for j in range(len(data) // 2):
        crc ^= int(data[j * 2:(j + 1) * 2], 16) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc >> 15) & 1 else (crc << 1)
            crc &= 0xFFFF
    return crc

def calc_crc2(data: str) -> int:
    """Calculate CRC-8 (CRC-8-ATM) for the given data."""
    crc = 0
    for char in data:
        crc ^= ord(char)
        for _ in range(8):
            crc = (crc << 1) ^ 0x99 if (crc & 0xFF) >> 7 else crc << 1
    return crc & 0xFF


def append_crc1(data: str) -> str:
    """Append CRC-16/ANSI X3.28 (CRC-16-IBM) to the given data."""
    return data + int_to_hex(calc_crc1(data), 4)


def append_crc2(data: str) -> str:
    """Append CRC-8 (CRC-8-ATM) to the given data."""
    return data + int_to_hex(calc_crc2(data), 2)


def make_pc_link_command(func: int, addr: str, args: bytes | None = None) -> str:
    """Construct a PC link command with the specified function, address, and optional arguments."""
    addr_int = int(addr, 16)
    data = (
        int_to_hex(func, 2)
        + int_to_hex((addr_int >> 0) & 0xFF, 2)
        + int_to_hex((addr_int >> 8) & 0xFF, 2)
    )
    if args:
        data += args.hex().upper()
    return append_crc2(f"${int_to_hex(len(data) + 10, 2)}{append_crc1(data)}")


def calculate_group_number(channel: int) -> int:
    """Calculate the group number of a channel."""
    return (channel + 5) // 6


def make_pc_link_inventory_command(payload):
    # Calculate CRC-16/ANSI
    crc1_result = calc_crc1(payload)

    # Calculate CRC-8/ATM with additional formatting
    intermediate_string = f"$14{payload}{crc1_result:04X}"
    crc2_result = calc_crc2(intermediate_string)

    return f"$14{payload}{crc1_result:04X}{crc2_result:02X}"


def reverse_24bit_to_hex(n: int) -> str:
    """
    Convert a decimal number to a 24-bit binary string,
    reverse (mirror) that string, and return the result as 6-digit hex.
    """
    # 1) Convert the number to a 24-bit binary string
    bin_24 = f"{n:024b}"

    # 2) Reverse the bit string
    reversed_bin = bin_24[::-1]

    # 3) Convert reversed binary to an integer
    reversed_int = int(reversed_bin, 2)

    # 4) Format as 6-digit hex (uppercase)
    reversed_hex = format(reversed_int, "06X")
    return reversed_hex


def nikobus_to_button_address(hex_address, button="1A"):
    """
    Convert a 24-bit Nikobus module 'hex_address' (e.g. '123456')
    into the special '#Nxxxxxx' form for the given 'button' (1A..2D).
    """

    # 3-bit codes for the 8 possible buttons
    button_map = {
        "1A": 0b101,
        "1B": 0b111,
        "1C": 0b001,
        "1D": 0b011,
        "2A": 0b100,
        "2B": 0b110,
        "2C": 0b000,
        "2D": 0b010,
    }
    if button not in button_map:
        raise ValueError(
            f"Unknown button '{button}'. Must be one of {list(button_map.keys())}."
        )

    # 1) Parse the original address as a 24-bit integer
    original_24 = int(hex_address, 16) & 0xFFFFFF

    # 2) Discard the two LSBs => shift right by 2
    shifted_22 = original_24 >> 2

    # 3) Prepend the 3 button bits on top (left side)
    btn_3bits = button_map[button]
    combined_24 = (btn_3bits << 21) | (shifted_22 & 0x1FFFFF)

    # 4) Reverse all 24 bits. (bit 0 <-> bit 23, etc.)
    def reverse_24bits(x):
        r = 0
        for i in range(24):
            r <<= 1
            r |= (x >> i) & 1
        return r

    reversed_24 = reverse_24bits(combined_24)

    # 5) Format as hex, uppercase, zero-padded to 6 digits, then prepend '#N'
    return "#N" + f"{reversed_24:06X}"


def nikobus_button_to_module(button_hex):
    """
    Given a Nikobus 'button address' of the form '#Nxxxxxx',
    reverse-engineer the original 6-hex-digit module address
    (with last 2 bits assumed zero) and which button (1A..2D).
    """
    # 1) Strip "#N" prefix and parse the remaining 6 hex digits
    if not button_hex.startswith("#N") or len(button_hex) != 8:
        raise ValueError(f"'{button_hex}' is not a valid '#Nxxxxxx' format.")

    reversed_hex = button_hex[2:]  # e.g. 'BA93EE'
    reversed_24 = int(reversed_hex, 16)  # parse as 24-bit hex

    # 2) Reverse all 24 bits to get 'combined_24'
    def reverse_24bits(x):
        r = 0
        for i in range(24):
            r <<= 1
            r |= (x >> i) & 1
        return r

    combined_24 = reverse_24bits(reversed_24)

    # 3) Extract the top 3 bits => the "button code"
    button_code = (combined_24 >> 21) & 0b111  # bits 23..21

    # 4) Extract the remaining 21 bits => the "shifted_22"
    shifted_22 = combined_24 & 0x1FFFFF  # bits 20..0

    # 5) Reconstruct the original 24-bit module address
    original_24 = (shifted_22 << 2) & 0xFFFFFF

    # 6) Translate 'button_code' back to a label
    inverse_button_map = {
        0b101: "1A",
        0b111: "1B",
        0b001: "1C",
        0b011: "1D",
        0b100: "2A",
        0b110: "2B",
        0b000: "2C",
        0b010: "2D",
    }

    button_label = inverse_button_map.get(button_code, "UNKNOWN")

    # 7) Format the module address as 6 hex digits, uppercase
    module_hex = f"{original_24:06X}"

    return module_hex, button_label
