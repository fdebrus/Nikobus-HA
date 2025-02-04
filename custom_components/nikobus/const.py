"""Constants for the Nikobus integration."""

from typing import Final

# General
DOMAIN: Final[str] = "nikobus"
BRAND: Final[str] = "Niko"

# Configuration Keys
CONF_CONNECTION_STRING: Final[str] = "connection_string"
CONF_REFRESH_INTERVAL: Final[str] = "refresh_interval"
CONF_HAS_FEEDBACK_MODULE: Final[str] = "has_feedback_module"
CONF_HAS_PC_LINK: Final[str] = "has_pc_link"

# Serial Connection
BAUD_RATE: Final[int] = 9600
COMMANDS_HANDSHAKE: Final[list[str]] = [
    "++++",
    "ATH0",
    "ATZ",
    "$10110000B8CF9D",
    "#L0",
    "#E0",
    "#L0",
    "#E1",
]
EXPECTED_HANDSHAKE_RESPONSE: Final[str] = "$0511"
HANDSHAKE_TIMEOUT: Final[int] = 60  # Timeout for handshake in seconds

# Buttons
REFRESH_DELAY: Final[float] = 0.5  # Delay before retrieving status after button press
DIMMER_DELAY: Final[int] = 1  # Delay before retrieving dimmer status
SHORT_PRESS: Final[int] = 1  # Short press duration in seconds
MEDIUM_PRESS: Final[int] = 2  # Medium press duration in seconds
LONG_PRESS: Final[int] = 3  # Long press duration in seconds

# Covers
COVER_DELAY_BEFORE_STOP: Final[int] = 1  # Delay before stopping cover movement

# Listener Commands
BUTTON_COMMAND_PREFIX: Final[str] = "#N"
IGNORE_ANSWER: Final[str] = "$0E"  # Unknown response
FEEDBACK_REFRESH_COMMAND: Final[tuple[str, str]] = ("$1012", "$1017")
FEEDBACK_MODULE_ANSWER: Final[str] = "$1C"
MANUAL_REFRESH_COMMAND: Final[tuple[str, str]] = ("$0512", "$0517")
COMMAND_PROCESSED: Final[tuple[str, str]] = ("$0515", "$0516")
DEVICE_ADDRESS_INVENTORY: Final[str] = "$18"
DEVICE_INVENTORY: Final[str] = "$0510"

# Command Execution
COMMAND_EXECUTION_DELAY: Final[float] = 0.7  # Delay between command executions
COMMAND_ACK_WAIT_TIMEOUT: Final[int] = 15  # Timeout for command ACK
COMMAND_ANSWER_WAIT_TIMEOUT: Final[int] = 5  # Timeout for each loop waiting for an answer
MAX_ATTEMPTS: Final[int] = 3  # Maximum retry attempts

# Inventory
DEVICE_TYPES = {
    # Known Device Types
    "01": {"Category": "Module", "Model": "05-000-02", "Channels": 12, "Name": "Switch Module"},
    "02": {"Category": "Module", "Model": "05-001-02", "Channels": 6, "Name": "Roller Shutter Module"},
    "03": {"Category": "Module", "Model": "05-007-02", "Channels": 12, "Name": "Dimmer Module"},
    "04": {"Category": "Button", "Model": "05-342", "Name": "Button with 2 Operation Points"},
    "06": {"Category": "Button", "Model": "05-346", "Name": "Button with 4 Operation Points"},
    "08": {"Category": "Module", "Model": "05-201", "Name": "PC Logic"},
    "09": {"Category": "Module", "Model": "05-002-02", "Channels": 4, "Name": "Compact Switch Module"},
    "0F": {"Category": "Module", "Model": "05-200", "Name": "PC Link"},
    "0C": {"Category": "Button", "Model": "05-348", "Name": "IR Button with 4 Operation Points"},
    "12": {"Category": "Button", "Model": "05-349", "Name": "Button with 8 Operation Points"},
    "1F": {"Category": "Button", "Model": "05-311", "Name": "RF Transmitter with 2 Operation Points"},
    "23": {"Category": "Button", "Model": "05-312", "Name": "RF Transmitter with 4 Operation Points"},
    "25": {"Category": "Button", "Model": "05-055", "Name": "All-Function Interface"},
    "3F": {"Category": "Button", "Model": "05-344", "Name": "Feedback Button with 2 Operation Points"},
    "40": {"Category": "Button", "Model": "05-347", "Name": "Feedback Button with 4 Operation Points"},
    "42": {"Category": "Module", "Model": "05-207", "Name": "Feedback Module"},
    "44": {"Category": "Button", "Model": "05-057", "Name": "Switch Interface"},
}

