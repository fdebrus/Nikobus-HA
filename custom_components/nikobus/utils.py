class Utils:
    @staticmethod
    def cancel(future):
        if future is not None:
            future.cancel()

    @staticmethod
    def convert_to_human_readable_nikobus_address(address_string):
        try:
            address = int(address_string, 16)
            nikobus_address = 0

            for i in range(21):
                nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)

            nikobus_address <<= 1
            button = (address >> 21) & 0x07

            return Utils.left_pad_with_zeros(hex(nikobus_address)[2:], 6) + ":" + Utils.map_button(button)

        except ValueError:
            return "[" + address_string + "]"

    @staticmethod
    def map_button(button_index):
        button_map = {
            0: "1",
            1: "5",
            2: "2",
            3: "6",
            4: "3",
            5: "7",
            6: "4",
            7: "8"
        }
        return button_map.get(button_index, "?")

    @staticmethod
    def left_pad_with_zeros(text, size):
        return text.zfill(size)

class CRCUtil:
    CRC_INIT = 0xFFFF
    POLYNOMIAL = 0x1021

    @staticmethod
    def append_crc(input: Optional[str]) -> Optional[str]:
        if input is None:
            return None

        check = CRCUtil.CRC_INIT

        for b in bytes.fromhex(input):
            for i in range(8):
                if ((b >> (7 - i) & 1) == 1) ^ ((check >> 15 & 1) == 1):
                    check = (check << 1) ^ CRCUtil.POLYNOMIAL
                else:
                    check = check << 1

        check &= CRCUtil.CRC_INIT
        checksum = f"{check:04x}".zfill(4).upper()
        return (input + checksum).upper()

    @staticmethod
    def append_crc2(input: str) -> str:
        check = 0

        for b in input.encode():
            check ^= b

            for i in range(8):
                if ((check & 0xFF) >> 7) != 0:
                    check = (check << 1) ^ 0x99
                else:
                    check = check << 1
                check &= 0xFF

        return input + f"{check:02x}".zfill(2).upper()
