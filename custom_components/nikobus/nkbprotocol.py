"""***FINAL*** Nikobus Protocol Utilities."""


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
