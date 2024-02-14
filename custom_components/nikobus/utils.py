from typing import Optional

def cancel(future: Optional['Future']) -> None:
    """
    Cancel a future if it is not None.
    
    :param future: The future to cancel, if not None.
    """
    if future is not None:
        future.cancel()

def convert_to_human_readable_nikobus_address(address_string: str) -> str:
    """
    Convert bus address to push button's address as seen in Nikobus PC software.
    
    :param address_string: String representing a bus Push Button's address.
    :return: Push button's address as seen in Nikobus PC software.
    """
    try:
        address = int(address_string, 16)
        nikobus_address = 0

        for i in range(21):
            nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)

        nikobus_address = (nikobus_address << 1)
        button = (address >> 21) & 0x07

        return left_pad_with_zeros(format(nikobus_address, 'x'), 6) + ":" + map_button(button)

    except ValueError:
        return f"[{address_string}]"

def map_button(button_index: int) -> str:
    """
    Maps a button index to its corresponding Nikobus button number.
    
    :param button_index: The index of the button.
    :return: The Nikobus button number as a string.
    """
    button_mapping = {
        0: "1",
        1: "5",
        2: "2",
        3: "6",
        4: "3",
        5: "7",
        6: "4",
        7: "8"
    }
    return button_mapping.get(button_index, "?")

def left_pad_with_zeros(text: str, size: int) -> str:
    """
    Left pads a string with zeros until it reaches the specified size.
    
    :param text: The text to pad.
    :param size: The desired size of the text.
    :return: The text left padded with zeros to the specified size.
    """
    return text.rjust(size, '0')
