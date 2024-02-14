import binascii

def hex_to_bytes(hex_str):
    """Convert a hex string to bytes."""
    return binascii.unhexlify(hex_str)

def left_pad_with_zeros(text, size):
    """Left pad the string with zeros until it reaches the specified size."""
    return text.rjust(size, '0')

def append_crc(input_str):
    """Calculate the CRC16-CCITT checksum on the input string and return the input string with the checksum appended."""
    if input_str is None:
        return None
    
    crc_init = 0xFFFF
    polynomial = 0x1021
    check = crc_init
    
    for b in hex_to_bytes(input_str):
        for i in range(8):
            bit = (b >> (7 - i)) & 1
            if bit ^ ((check >> 15) & 1):
                check = (check << 1) ^ polynomial
            else:
                check = check << 1
            check &= 0xFFFF
    
    checksum = left_pad_with_zeros(format(check, 'x'), 4)
    return (input_str + checksum).upper()

def append_crc2(input_str):
    """Calculate the second checksum on the input string and return the input string with the checksum appended."""
    check = 0
    
    for b in input_str.encode():
        check ^= b
        for i in range(8):
            if (check & 0xff) >> 7:
                check = (check << 1) ^ 0x99
            else:
                check <<= 1
            check &= 0xFF
    
    return input_str + left_pad_with_zeros(format(check, 'x'), 2).upper()
