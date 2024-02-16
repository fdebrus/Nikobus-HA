"""Nikobus helpers"""

import math

def intToHex(value, digits):
    return ('00000000' + format(value, 'x').upper())[-digits:]

def hexToInt(value):
    return int(value, 16)

def intToDec(value, digits):
    return ('00000000' + str(value)).upper()[-digits:]

def decToInt(value):
    return int(value, 10)

def calcCRC1(data):
    crc = 0xFFFF
    for j in range(len(data) // 2):
        crc ^= (int(data[j * 2: (j + 1) * 2], 16) << 8)
        for i in range(8):
            if (crc >> 15) & 1 != 0:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
    return crc & 0xFFFF

def calcCRC2(data):
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

def appendCRC1(data):
    return data + intToHex(calcCRC1(data), 4)

def appendCRC2(data):
    return data + intToHex(calcCRC2(data), 2)

def MakePcLinkCommand(func, addr, args=None):
    addr_int = int(addr, 16)
    data = intToHex(func, 2) + intToHex((addr_int >> 0) & 0xFF, 2) + intToHex((addr_int >> 8) & 0xFF, 2)
    if args is not None:
        data += args
    return appendCRC2('$' + intToHex(len(data) + 10, 2) + appendCRC1(data))

def CalculateGroupOutputNumber(channel):
    group_output_number = (int(channel) - 1) % 6
    return group_output_number

def CalculateGroupNumber(channel):
    group_number = math.floor((int(channel) + 5) / 6)
    return group_number