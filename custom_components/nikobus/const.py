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
COMMAND_ANSWER_WAIT_TIMEOUT: Final[int] = (
    5  # Timeout for each loop waiting for an answer
)
MAX_ATTEMPTS: Final[int] = 3  # Maximum retry 

# Discovery
DEVICE_TYPES = {
    "01": {"Category": "Module", "Model": "05-000-02", "Channels": 12, "Name": "Switch Module"},
    "02": {"Category": "Module", "Model": "05-001-02", "Channels": 6, "Name": "Roller Shutter Module"},
    "03": {"Category": "Module", "Model": "05-007-02", "Channels": 12, "Name": "Dimmer Module"},
    "04": {"Category": "Button", "Model": "05-342", "Channels": 2, "Name": "Button with 2 Operation Points"},
    "06": {"Category": "Button", "Model": "05-346", "Channels": 4, "Name": "Button with 4 Operation Points"},
    "08": {"Category": "Module", "Model": "05-201", "Name": "PC Logic"},
    "09": {"Category": "Module", "Model": "05-002-02", "Channels": 4, "Name": "Compact Switch Module"},
    "0A": {"Category": "Module", "Model": "05-200", "Name": "PC Link"},
    "0C": {"Category": "Button", "Model": "05-348", "Channels": 4, "Name": "IR Button with 4 Operation Points"},
    "12": {"Category": "Button", "Model": "05-349", "Channels": 8, "Name": "Button with 8 Operation Points"},
    "1F": {"Category": "Button", "Model": "05-311", "Channels": 2, "Name": "RF Transmitter with 2 Operation Points"},
    "23": {"Category": "Button", "Model": "05-312", "Channels": 4, "Name": "RF Transmitter with 4 Operation Points"},
    "25": {"Category": "Button", "Model": "05-055", "Channels": 4, "Name": "All-Function Interface"},
    "31": {"Category": "Module", "Model": "05-002-02", "Channels": 4, "Name": "Compact Switch Module"},
    "3F": {"Category": "Button", "Model": "05-344", "Channels": 2, "Name": "Feedback Button with 2 Operation Points"},
    "40": {"Category": "Button", "Model": "05-347", "Channels": 4, "Name": "Feedback Button with 4 Operation Points"},
    "42": {"Category": "Module", "Model": "05-207", "Name": "Feedback Module"},
    "44": {"Category": "Button", "Model": "05-057", "Channels": 4, "Name": "Switch Interface"},
}

MODE_MAPPING = {
    0: "M01 On/Off",
    1: "M02 On with operating time",
    2: "M03 Off with operation time",
    3: "M04 Pushbutton",
    4: "M05 Impulse",
    5: "M06 Delayed off (long up to 2h)",
    6: "M07 Delayed on (long up to 2h)",
    7: "M08 Flashing",
    8: "M11 Delayed off (short up to 50s)",
    9: "M12 Delayed on (short up to 50s)",
    11: "M14 Light scene on",
    12: "M15 Light scene on / off",
}

TIMER_MAPPING = {
    0: ["10s", "0.5s", "0s"],
    1: ["1m", "1s", "1s"],
    2: ["2m", "2s", "2s"],
    3: ["3m", "3s", "3s"],
    4: ["4m", "4s", None],
    5: ["5m", "5s", None],
    6: ["6m", "6s", None],
    7: ["7m", "7s", None],
    8: ["8m", "8s", None],
    9: ["9m", "9s", None],
    10: ["15m", "15s", None],
    11: ["30m", "20s", None],
    12: ["45m", "25s", None],
    13: ["60m", "30s", None],
    14: ["90m", "40s", None],
    15: ["120m", "50s", None],
}

KEY_MAPPING = {
    2: {"1A": "8", "1B": "C"},
    4: {"1A": "8", "1B": "C", "1C": "0", "1D": "4"},
    8: {
        "1A": "A",
        "1B": "E",
        "1C": "2",
        "1D": "6",
        "2A": "8",
        "2B": "C",
        "2C": "0",
        "2D": "4",
    },
}

KEY_MAPPING2 = {0: "1C", 1: "1A", 2: "1D", 3: "1B", 4: "2C", 5: "2A", 6: "2D", 7: "2B"}

CHANNEL_MAPPING = {
    0: "Channel 1",
    1: "Channel 2",
    2: "Channel 3",
    3: "Channel 4",
    4: "Channel 5",
    5: "Channel 6",
    6: "Channel 7",
    7: "Channel 8",
    8: "Channel 9",
    9: "Channel 10",
    10: "Channel 11",
    11: "Channel 12",
}
