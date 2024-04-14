"""Nikobus Protocol"""

def int_to_hex(value, digits):
    """Convert an integer to a hexadecimal string with specified number of digits"""
    return ('00000000' + format(value, 'x').upper())[-digits:]

def calc_crc1(data):
    """Calculate CRC-16/ANSI X3.28 (CRC-16-IBM) for the given data"""
    crc = 0xFFFF
    for j in range(len(data) // 2):
        crc ^= (int(data[j * 2: (j + 1) * 2], 16) << 8)
        for i in range(8):
            if (crc >> 15) & 1 != 0:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
    return crc & 0xFFFF

def calc_crc2(data):
    """Calculate CRC-8 (CRC-8-ATM) for the given data"""
    crc = 0
    for char in data:
        crc ^= ord(char)
        for _ in range(8):
            if (crc & 0xFF) >> 7 != 0:
                crc = crc << 1
                crc ^= 0x99
            else:
                crc = crc << 1
    return crc & 0xFF

def append_crc1(data):
    """Append CRC-16/ANSI X3.28 (CRC-16-IBM) to the given data"""
    return data + int_to_hex(calc_crc1(data), 4)

def append_crc2(data):
    """Append CRC-8 (CRC-8-ATM) to the given data"""
    return data + int_to_hex(calc_crc2(data), 2)

def make_pc_link_command(func, addr, args=None):
    """Construct a PC link command with the specified function, address, and optional arguments"""
    addr_int = int(addr, 16)
    data = int_to_hex(func, 2) + int_to_hex((addr_int >> 0) & 0xFF, 2) + int_to_hex((addr_int >> 8) & 0xFF, 2)
    if args is not None:
        args_hex = args.hex().upper()
        data += args_hex
    return append_crc2('$' + int_to_hex(len(data) + 10, 2) + append_crc1(data))

def calculate_group_number(channel):
    """Calculate the group number of a channel"""
    return (int(channel) + 5) // 6
