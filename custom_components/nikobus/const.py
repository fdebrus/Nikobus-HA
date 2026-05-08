"""Constants for the Nikobus integration."""

from typing import Final

# =============================================================================
# General
# =============================================================================
DOMAIN: Final[str] = "nikobus"
BRAND: Final[str] = "Niko"
HUB_IDENTIFIER: Final[str] = "nikobus_hub"

# =============================================================================
# Events
# =============================================================================
EVENT_BUTTON_OPERATION: Final[str] = "nikobus_button_operation"
EVENT_BUTTON_PRESSED: Final[str] = "nikobus_button_pressed"

# =============================================================================
# Discovery
# =============================================================================
# Device-type / channel / key / mode / timer mapping tables live in
# ``nikobus_connect.discovery.mapping`` and are owned by the library —
# ``classify_device_type()`` writes the resolved name + model + channel
# count straight into the discovery output, which the HA-side platform
# code reads via ``phys.get("type")`` etc. Don't shadow them here: a
# stale local copy is what made the integration look like it was doing
# its own classification when it isn't.

SIGNAL_DISCOVERY_STATE: Final[str] = "nikobus_discovery_state"

DISCOVERY_PHASE_IDLE: Final[str] = "idle"
DISCOVERY_PHASE_PC_LINK: Final[str] = "pc_link"
DISCOVERY_PHASE_MODULE_SCAN: Final[str] = "module_scan"
DISCOVERY_PHASE_FINISHED: Final[str] = "finished"
DISCOVERY_PHASE_ERROR: Final[str] = "error"

# Fine-grained sub-phases exposed via ``discovery_sub_phase``. The high-level
# ``discovery_phase`` stays on the legacy enum so existing automations keep
# working: ``inventory`` + ``identity`` map back to ``pc_link``;
# ``register_scan`` + ``finalizing`` map back to ``module_scan``.
DISCOVERY_SUB_PHASE_IDLE: Final[str] = "idle"
DISCOVERY_SUB_PHASE_INVENTORY: Final[str] = "inventory"
DISCOVERY_SUB_PHASE_IDENTITY: Final[str] = "identity"
DISCOVERY_SUB_PHASE_REGISTER_SCAN: Final[str] = "register_scan"
DISCOVERY_SUB_PHASE_FINALIZING: Final[str] = "finalizing"
DISCOVERY_SUB_PHASE_FINISHED: Final[str] = "finished"
DISCOVERY_SUB_PHASE_ERROR: Final[str] = "error"

# Weighting for the 0-100 progress sensor. Must sum to 100.
DISCOVERY_WEIGHT_INVENTORY: Final[int] = 10
DISCOVERY_WEIGHT_IDENTITY: Final[int] = 20
DISCOVERY_WEIGHT_REGISTER_SCAN: Final[int] = 65
DISCOVERY_WEIGHT_FINALIZING: Final[int] = 5

# =============================================================================
# Repair issues
# =============================================================================
ISSUE_NO_BUTTONS_CONFIGURED: Final[str] = "no_buttons_configured"

# =============================================================================
# Configuration Keys
# =============================================================================
CONF_CONNECTION_STRING: Final[str] = "connection_string"
CONF_REFRESH_INTERVAL: Final[str] = "refresh_interval"
CONF_HAS_FEEDBACK_MODULE: Final[str] = "has_feedbackmodule"
CONF_PRIOR_GEN3: Final[str] = "prior_gen3"

# =============================================================================
# Serial Connection
# =============================================================================
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
DIMMER_DELAY: Final[int] = 1  # Delay before retrieving dimmer status
SHORT_PRESS: Final[float] = 1.0  # Short press duration in seconds
BUTTON_TIMER_THRESHOLDS: Final[tuple[int, int, int]] = (1, 2, 3)

# =============================================================================
# Covers
# =============================================================================
DEFAULT_COVER_ASSUMED_STATE: Final[bool] = False
DEFAULT_COVER_MOVEMENT_BUFFER: Final[float] = 3.0
DEFAULT_COVER_DEBOUNCE_DELAY: Final[float] = 0.3
DEFAULT_COVER_OPERATION_TIME: Final[float] = 30.0

# =============================================================================
# Listener
# =============================================================================
BUTTON_COMMAND_PREFIX: Final[str] = "#N"
FEEDBACK_REFRESH_COMMAND: Final[tuple[str, str]] = ("$1012", "$1017")
FEEDBACK_MODULE_ANSWER: Final[str] = "$1C"
MANUAL_REFRESH_COMMAND: Final[tuple[str, str]] = ("$0512", "$0517")
COMMAND_PROCESSED: Final[tuple[str, str]] = ("$0515", "$0516")
DEVICE_ADDRESS_INVENTORY: Final[str] = "$18"
DEVICE_INVENTORY_ANSWER: Final[tuple[str, str]] = ("$2E", "$1E")

# =============================================================================
# Reconnect
# =============================================================================
RECONNECT_DELAY_INITIAL: Final[int] = 5   # First retry delay in seconds
RECONNECT_DELAY_MAX: Final[int] = 60      # Cap on exponential-backoff delay

# =============================================================================
# Command Execution
# =============================================================================
COMMAND_EXECUTION_DELAY: Final[float] = 0.15  # Delay between command executions (OH1 uses 50 ms; 150 ms gives the bus a safe clearing window)
COMMAND_ACK_WAIT_TIMEOUT: Final[int] = 15   # Outer deadline for the whole ACK+ANSWER wait
COMMAND_ANSWER_WAIT_TIMEOUT: Final[int] = 5  # Pre-ACK: how long to wait for the ACK itself
COMMAND_POST_ACK_ANSWER_TIMEOUT: Final[float] = 1.5  # Post-ACK: data should follow ACK quickly
MAX_ATTEMPTS: Final[int] = 3  # Maximum retry attempts
