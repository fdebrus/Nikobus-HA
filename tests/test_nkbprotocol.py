"""Tests for custom_components.nikobus.nkbprotocol — pure protocol utilities."""

import unittest

from custom_components.nikobus.nkbprotocol import (
    _reverse_bits,
    append_crc1,
    append_crc2,
    calc_crc1,
    calc_crc2,
    calculate_group_number,
    int_to_hex,
    make_pc_link_command,
    nikobus_button_to_module,
    nikobus_to_button_address,
    reverse_24bit_to_hex,
)


# ---------------------------------------------------------------------------
# int_to_hex
# ---------------------------------------------------------------------------

class TestIntToHex(unittest.TestCase):
    def test_zero_two_digits(self):
        self.assertEqual(int_to_hex(0, 2), "00")

    def test_max_byte(self):
        self.assertEqual(int_to_hex(255, 2), "FF")

    def test_four_digit_padding(self):
        self.assertEqual(int_to_hex(1, 4), "0001")

    def test_four_digit_value(self):
        self.assertEqual(int_to_hex(0xABCD, 4), "ABCD")

    def test_uppercase_output(self):
        self.assertEqual(int_to_hex(0xAB, 2), "AB")

    def test_overflow_uses_more_digits(self):
        # Value wider than `digits` should still render completely
        self.assertEqual(int_to_hex(256, 2), "100")


# ---------------------------------------------------------------------------
# calc_crc1  (CRC-16/ANSI X3.28)
# ---------------------------------------------------------------------------

class TestCalcCrc1(unittest.TestCase):
    def test_returns_int_in_range(self):
        result = calc_crc1("1500C1C7")
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 0xFFFF)

    def test_deterministic(self):
        data = "12C7C1AABB"
        self.assertEqual(calc_crc1(data), calc_crc1(data))

    def test_different_data_different_crc(self):
        self.assertNotEqual(calc_crc1("1500C1C7"), calc_crc1("1700C1C7"))

    def test_invalid_hex_raises(self):
        with self.assertRaises(Exception):
            calc_crc1("GGGG")


# ---------------------------------------------------------------------------
# calc_crc2  (CRC-8/ATM)
# ---------------------------------------------------------------------------

class TestCalcCrc2(unittest.TestCase):
    def test_returns_byte(self):
        result = calc_crc2("$1CC7C1")
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 0xFF)

    def test_deterministic(self):
        s = "$1CC7C10000112233"
        self.assertEqual(calc_crc2(s), calc_crc2(s))

    def test_different_inputs_differ(self):
        self.assertNotEqual(calc_crc2("ABC"), calc_crc2("ABD"))

    def test_empty_string_returns_zero(self):
        self.assertEqual(calc_crc2(""), 0)


# ---------------------------------------------------------------------------
# append_crc1 / append_crc2
# ---------------------------------------------------------------------------

class TestAppendCrc(unittest.TestCase):
    def test_append_crc1_length(self):
        data = "12C7C1"
        result = append_crc1(data)
        self.assertEqual(len(result), len(data) + 4)

    def test_append_crc1_prefix(self):
        data = "12C7C1"
        self.assertTrue(append_crc1(data).startswith(data))

    def test_append_crc2_length(self):
        s = "$1412C7C1ABCD"
        result = append_crc2(s)
        self.assertEqual(len(result), len(s) + 2)

    def test_append_crc2_prefix(self):
        s = "$14AABB"
        self.assertTrue(append_crc2(s).startswith(s))


# ---------------------------------------------------------------------------
# make_pc_link_command
# ---------------------------------------------------------------------------

class TestMakePcLinkCommand(unittest.TestCase):
    """make_pc_link_command produces a CRC-valid bus frame."""

    def _cmd(self, func=0x12, addr="C1C7", args=None):
        return make_pc_link_command(func, addr, args)

    def test_starts_with_dollar(self):
        self.assertTrue(self._cmd().startswith("$"))

    def test_address_little_endian_in_frame(self):
        # Address C1C7 in little-endian is C7C1
        cmd = make_pc_link_command(0x12, "C1C7")
        self.assertIn("C7C1", cmd)

    def test_func_code_in_frame(self):
        # Group-1 GET uses func 0x12
        cmd = make_pc_link_command(0x12, "C1C7")
        self.assertIn("12", cmd)

    def test_group2_func_code(self):
        cmd = make_pc_link_command(0x17, "C1C7")
        self.assertIn("17", cmd)

    def test_set_command_with_args(self):
        args = bytearray([0xFF, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xFF])
        cmd = make_pc_link_command(0x15, "C1C7", args)
        self.assertTrue(cmd.startswith("$"))

    def test_frame_passes_validate_crc(self):
        """The frame produced must pass the same CRC logic used by the listener."""
        from custom_components.nikobus.nkblistener import NikobusEventListener
        # validate_crc is a plain method; create a minimal listener
        from unittest.mock import MagicMock
        listener = NikobusEventListener.__new__(NikobusEventListener)
        listener._frame_buffer = ""

        for func, addr in [(0x12, "C1C7"), (0x17, "C1C7"), (0x15, "AABB"), (0x16, "1234")]:
            cmd = make_pc_link_command(func, addr)
            self.assertTrue(
                listener.validate_crc(cmd),
                f"validate_crc failed for func={func:#04x} addr={addr} cmd={cmd}",
            )

    def test_length_field_consistent(self):
        cmd = make_pc_link_command(0x12, "C1C7")
        length_field = int(cmd[1:3], 16)
        # Length field = len(frame) + 1
        self.assertEqual(len(cmd), length_field - 1)


# ---------------------------------------------------------------------------
# calculate_group_number
# ---------------------------------------------------------------------------

class TestCalculateGroupNumber(unittest.TestCase):
    def test_channels_1_to_6_are_group_1(self):
        for ch in range(1, 7):
            with self.subTest(channel=ch):
                self.assertEqual(calculate_group_number(ch), 1)

    def test_channels_7_to_12_are_group_2(self):
        for ch in range(7, 13):
            with self.subTest(channel=ch):
                self.assertEqual(calculate_group_number(ch), 2)

    def test_boundary_channel_6(self):
        self.assertEqual(calculate_group_number(6), 1)

    def test_boundary_channel_7(self):
        self.assertEqual(calculate_group_number(7), 2)


# ---------------------------------------------------------------------------
# _reverse_bits
# ---------------------------------------------------------------------------

class TestReverseBits(unittest.TestCase):
    def test_single_bit_1(self):
        self.assertEqual(_reverse_bits(1, 1), 1)

    def test_single_bit_0(self):
        self.assertEqual(_reverse_bits(0, 1), 0)

    def test_8bit_msb_becomes_lsb(self):
        # 0b10000000 reversed in 8 bits → 0b00000001
        self.assertEqual(_reverse_bits(0b10000000, 8), 0b00000001)

    def test_8bit_pattern(self):
        # 0b10110100 → 0b00101101
        self.assertEqual(_reverse_bits(0b10110100, 8), 0b00101101)

    def test_4bit_alternating(self):
        # 0b1010 reversed in 4 bits → 0b0101
        self.assertEqual(_reverse_bits(0b1010, 4), 0b0101)

    def test_identity_palindrome(self):
        # 0b00000000 reversed = 0
        self.assertEqual(_reverse_bits(0, 8), 0)


# ---------------------------------------------------------------------------
# reverse_24bit_to_hex
# ---------------------------------------------------------------------------

class TestReverse24BitToHex(unittest.TestCase):
    def test_returns_6_char_hex(self):
        result = reverse_24bit_to_hex(0x123456)
        self.assertEqual(len(result), 6)

    def test_output_uppercase(self):
        result = reverse_24bit_to_hex(0xABCDEF)
        self.assertEqual(result, result.upper())

    def test_zero_is_all_zeros(self):
        self.assertEqual(reverse_24bit_to_hex(0), "000000")

    def test_all_ones_remain_all_ones(self):
        # 0xFFFFFF bit-reversed is still 0xFFFFFF
        self.assertEqual(reverse_24bit_to_hex(0xFFFFFF), "FFFFFF")

    def test_known_reversal(self):
        # 0x800000 = bit 23 set → reversed → bit 0 set = 0x000001
        self.assertEqual(reverse_24bit_to_hex(0x800000), "000001")


# ---------------------------------------------------------------------------
# nikobus_to_button_address / nikobus_button_to_module  (roundtrip)
# ---------------------------------------------------------------------------

class TestButtonAddressRoundtrip(unittest.TestCase):
    """Every button label must survive a to→from roundtrip without corruption.

    The encoding discards the 2 LSBs of the module address (hardware behavior:
    Nikobus bus addresses are always 4-byte aligned, so bits 0-1 are always 0).
    Additionally, the internal 21-bit mask means addresses must be < 0x800000.
    Valid test addresses: multiples of 4 in the range [0x000000, 0x7FFFFC].
    """

    ALL_BUTTONS = ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"]

    # These are valid Nikobus module addresses: multiples of 4 and < 0x800000.
    VALID_ADDR_A = "1A2B3C"   # 0x1A2B3C % 4 == 0, < 0x800000
    VALID_ADDR_B = "3BCDE0"   # 0x3BCDE0 % 4 == 0, < 0x800000
    VALID_MAX    = "7FFFFC"   # largest valid address (0x7FFFFC)

    def _roundtrip(self, module_addr: str, btn: str):
        frame = nikobus_to_button_address(module_addr, btn)
        recovered_addr, recovered_btn = nikobus_button_to_module(frame)
        return frame, recovered_addr, recovered_btn

    def test_frame_prefix_and_length(self):
        frame = nikobus_to_button_address(self.VALID_ADDR_A, "1A")
        self.assertTrue(frame.startswith("#N"))
        self.assertEqual(len(frame), 8)

    def test_roundtrip_all_buttons_valid_addr_a(self):
        for btn in self.ALL_BUTTONS:
            with self.subTest(button=btn):
                frame, addr, recovered_btn = self._roundtrip(self.VALID_ADDR_A, btn)
                self.assertEqual(addr, self.VALID_ADDR_A)
                self.assertEqual(recovered_btn, btn)

    def test_roundtrip_all_buttons_valid_addr_b(self):
        for btn in self.ALL_BUTTONS:
            with self.subTest(button=btn):
                frame, addr, recovered_btn = self._roundtrip(self.VALID_ADDR_B, btn)
                self.assertEqual(addr, self.VALID_ADDR_B)
                self.assertEqual(recovered_btn, btn)

    def test_roundtrip_zero_address(self):
        frame, addr, btn = self._roundtrip("000000", "2C")
        self.assertEqual(addr, "000000")
        self.assertEqual(btn, "2C")

    def test_roundtrip_max_valid_address(self):
        frame, addr, btn = self._roundtrip(self.VALID_MAX, "1D")
        self.assertEqual(addr, self.VALID_MAX)
        self.assertEqual(btn, "1D")

    def test_two_lsbs_discarded_by_design(self):
        """Addresses that differ only in their 2 LSBs encode to the same frame."""
        # 0x1A2B3C and 0x1A2B3F differ in bits 0-1 only
        frame_base = nikobus_to_button_address("1A2B3C", "1A")
        frame_unaligned = nikobus_to_button_address("1A2B3F", "1A")
        self.assertEqual(frame_base, frame_unaligned)

    def test_different_buttons_give_different_frames(self):
        frames = {
            btn: nikobus_to_button_address(self.VALID_ADDR_A, btn)
            for btn in self.ALL_BUTTONS
        }
        self.assertEqual(len(set(frames.values())), len(self.ALL_BUTTONS))

    def test_invalid_button_raises_value_error(self):
        with self.assertRaises(ValueError):
            nikobus_to_button_address(self.VALID_ADDR_A, "9X")

    def test_invalid_frame_too_short_raises(self):
        with self.assertRaises(ValueError):
            nikobus_button_to_module("TOOSHORT")

    def test_invalid_frame_wrong_prefix_raises(self):
        with self.assertRaises(ValueError):
            nikobus_button_to_module("$N123456")

    def test_button_1a_known_frame_format(self):
        frame = nikobus_to_button_address("1A2B3C", "1A")
        self.assertEqual(len(frame), 8)
        self.assertTrue(frame.startswith("#N"))


if __name__ == "__main__":
    unittest.main()
