"""Discovery-specific constants for the Nikobus protocol."""

from typing import Final

DEVICE_ADDRESS_INVENTORY: Final[str] = "$18"
DEVICE_INVENTORY_ANSWER: Final[tuple[str, str]] = ("$2E", "$1E")
