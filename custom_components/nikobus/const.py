"""Constants for the Nikobus integration."""

from typing import Final

# =============================================================================
# General
# =============================================================================
DOMAIN: Final[str] = "nikobus"
BRAND: Final[str] = "Niko"

# =============================================================================
# Configuration Keys
# =============================================================================
CONF_CONNECTION_STRING: Final[str] = "connection_string"
CONF_REFRESH_INTERVAL: Final[str] = "refresh_interval"
CONF_HAS_FEEDBACK_MODULE: Final[str] = "has_feedbackmodule"
CONF_HAS_PC_LINK: Final[str] = "has_pclink"

# =============================================================================
# Serial Connection
# =============================================================================
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

# =============================================================================
# Buttons
# =============================================================================
REFRESH_DELAY: Final[float] = 0.5  # Delay before retrieving status after button press
DIMMER_DELAY: Final[int] = 1       # Delay before retrieving dimmer status
SHORT_PRESS: Final[int] = 1        # Short press duration in seconds
MEDIUM_PRESS: Final[int] = 2       # Medium press duration in seconds
LONG_PRESS: Final[int] = 3         # Long press duration in seconds

# =============================================================================
# Covers
# =============================================================================
COVER_DELAY_BEFORE_STOP: Final[int] = 1  # Delay before stopping cover movement

# =============================================================================
# Listener
# =============================================================================
BUTTON_COMMAND_PREFIX: Final[str] = "#N"
IGNORE_ANSWER: Final[str] = "$0E"  # Unknown response
FEEDBACK_REFRESH_COMMAND: Final[tuple[str, str]] = ("$1012", "$1017")
FEEDBACK_MODULE_ANSWER: Final[str] = "$1C"
MANUAL_REFRESH_COMMAND: Final[tuple[str, str]] = ("$0512", "$0517")
COMMAND_PROCESSED: Final[tuple[str, str]] = ("$0515", "$0516")
DEVICE_ADDRESS_INVENTORY: Final[str] = "$18"
DEVICE_INVENTORY: Final[tuple[str, str]] = ("$0510$2E", "$0522$1E")

# =============================================================================
# Command Execution
# =============================================================================
COMMAND_EXECUTION_DELAY: Final[float] = 0.7    # Delay between command executions
COMMAND_ACK_WAIT_TIMEOUT: Final[int] = 15      # Timeout for command ACK
COMMAND_ANSWER_WAIT_TIMEOUT: Final[int] = 5    # Timeout for each loop waiting for an answer
MAX_ATTEMPTS: Final[int] = 3                   # Maximum retry attempts

# =============================================================================
# Discovery
# =============================================================================
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
    "25": {"Category": "Button", "Model": "05-311", "Channels": 1, "Name": "Portable RF Transmitter with 1 Operation Point"},
    "31": {"Category": "Module", "Model": "05-002-02", "Channels": 4, "Name": "Compact Switch Module"},
    "3F": {"Category": "Button", "Model": "05-344", "Channels": 2, "Name": "Feedback Button with 2 Operation Points"},
    "40": {"Category": "Button", "Model": "05-347", "Channels": 4, "Name": "Feedback Button with 4 Operation Points"},
    "42": {"Category": "Module", "Model": "05-207", "Name": "Feedback Module"},
    "44": {"Category": "Button", "Model": "05-058", "Channels": 8, "Name": "Switch Interface"},
}

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

KEY_MAPPING = {
    1: {"1A": "8"},
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

KEY_MAPPING_MODULE = {
    1: {1: "8"},
    2: {1: "8", 3: "C"},
    4: {0: "0", 1: "8", 2: "4", 3: "C"},
    8: {0: "0", 1: "8", 2: "4", 3: "C", 4: "2", 5: "A", 6: "6", 7: "E"},
}

IR_CHANNEL_MAPPING = {
    1: "A",
    3: "B",
    0: "C",
    2: "D",
}

KEY_MAPPING_IR = {
    "0": "01A",   "1": "01B",   "2": "01C",   "3": "01D",
    "4": "02A",   "5": "02B",   "6": "02C",   "7": "02D",
    "8": "03A",   "9": "03B",  "10": "03C",  "11": "03D",
    "12": "04A", "13": "04B", "14": "04C", "15": "04D",
    "16": "05A", "17": "05B", "18": "05C", "19": "05D",
    "20": "06A", "21": "06B", "22": "06C", "23": "06D",
    "24": "07A", "25": "07B", "26": "07C", "27": "07D",
    "28": "08A", "29": "08B", "30": "08C", "31": "08D",
    "32": "09A", "33": "09B", "34": "09C", "35": "09D",
    "36": "10A", "37": "10B", "38": "10C", "39": "10D",
    "40": "11A", "41": "11B", "42": "11C", "43": "11D",
    "44": "12A", "45": "12B", "46": "12C", "47": "12D",
    "48": "13A", "49": "13B", "50": "13C", "51": "13D",
    "52": "14A", "53": "14B", "54": "14C", "55": "14D",
    "56": "15A", "57": "15B", "58": "15C", "59": "15D",
    "60": "16A", "61": "16B", "62": "16C", "63": "16D",
    "64": "17A", "65": "17B", "66": "17C", "67": "17D",
    "68": "18A", "69": "18B", "70": "18C", "71": "18D",
    "72": "19A", "73": "19B", "74": "19C", "75": "19D",
    "76": "20A", "77": "20B", "78": "20C", "79": "20D",
    "80": "21A", "81": "21B", "82": "21C", "83": "21D",
    "84": "22A", "85": "22B", "86": "22C", "87": "22D",
    "88": "23A", "89": "23B", "90": "23C", "91": "23D",
    "92": "24A", "93": "24B", "94": "24C", "95": "24D",
    "96": "25A", "97": "25B", "98": "25C", "99": "25D",
    "100": "26A", "101": "26B", "102": "26C", "103": "26D",
    "104": "27A", "105": "27B", "106": "27C", "107": "27D",
    "108": "28A", "109": "28B", "110": "28C", "111": "28D",
    "112": "29A", "113": "29B", "114": "29C", "115": "29D",
    "116": "30A", "117": "30B", "118": "30C", "119": "30D",
    "120": "31A", "121": "31B", "122": "31C", "123": "31D",
    "124": "32A", "125": "32B", "126": "32C", "127": "32D",
    "128": "33A", "129": "33B", "130": "33C", "131": "33D",
    "132": "34A", "133": "34B", "134": "34C", "135": "34D",
    "136": "35A", "137": "35B", "138": "35C", "139": "35D",
    "140": "36A", "141": "36B", "142": "36C", "143": "36D",
    "144": "37A", "145": "37B", "146": "37C", "147": "37D",
    "148": "38A", "149": "38B", "150": "38C", "151": "38D",
    "152": "39A", "153": "39B", "154": "39C", "155": "39D",
}

# =============================================================================
# Switch
# =============================================================================
SWITCH_MODE_MAPPING = {
    0: "M01 (On / off)",
    1: "M02 (On, with operating time)",
    2: "M03 (Off, with operation time)",
    3: "M04 (Pushbutton)",
    4: "M05 (Impulse)",
    5: "M06 (Delayed off (long up to 2h))",
    6: "M07 (Delayed on (long up to 2h))",
    7: "M08 (Flashing)",
    8: "M11 (Delayed off (short up to 50sec.))",
    9: "M12 (Delayed on (short up to 50sec.))",
    10: "M14 (Light scene on)",
    11: "M15 (Light scene on / off)",
}

SWITCH_TIMER_MAPPING = {
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

# =============================================================================
# Roller
# =============================================================================
ROLLER_MODE_MAPPING = {
    0: "M01 (Open - stop - close)",
    1: "M02 (Open)",
    2: "M03 (Close)",
    3: "M04 (Stop)",
    4: "M05 (Interface- and RF-control)",
    5: "M06 (Open with operating time)",
    6: "M07 (Close with operating time)",
}

ROLLER_TIMER_MAPPING = {
    0: ["Turned off", None, None],
    1: ["0,4 s (impuls)", None, None],
    2: ["6 s", None, None],
    3: ["8 s", None, None],
    4: ["10 s", None, None],
    5: ["12 s", None, None],
    6: ["6 s", None, None],
    7: ["14 s", None, None],
    8: ["16 s", None, None],
    9: ["18 s", None, None],
    10: ["20 s", None, None],
    11: ["25 s", None, None],
    12: ["30 s", None, None],
    13: ["40 s", None, None],
    14: ["50 s", None, None],
    15: ["60 s", None, None],
    16: ["90 s", None, None],
}

# =============================================================================
# Dimmer
# =============================================================================
DIMMER_MODE_MAPPING = {
    0: "M01 (Dim on/off (2 buttons))",
    1: "M02 (Dim on/off (4 buttons))",
    2: "M03 (Light scene on/off)",
    3: "M04 (Light scene on)",
    4: "M05 (On (if necessary with operating time))",
    5: "M06 (Off (eventually with operating time))",
    6: "M07 (Delayed off)",
    7: "M11 (Preset on/off)",
    8: "M12 (Preset on)",
}

DIMMER_TIMER_MAPPING = {
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
