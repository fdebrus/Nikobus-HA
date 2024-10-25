"""Constants"""
DOMAIN = "nikobus"
BRAND = "Niko"

# Configuration
CONF_CONNECTION_STRING = "connection_string"
CONF_REFRESH_INTERVAL = "refresh_interval"
CONF_HAS_FEEDBACK_MODULE = "has_feedback_module"
CONF_HAS_PC_LINK = "has_pc_link"

# Buttons
DIMMER_DELAY = 1 # When a dimmer button is pressed, pause for DIMMER_DELAY before to retrieve status from NIkobus
LONG_PRESS_THRESHOLD_MS = 500 # Time in ms to detect a long press (>= LONG_PRESS_THRESHOLD_MS)
SHORT_PRESS = 1  # Duration in seconds that classifies a button press as a short press
MEDIUM_PRESS = 2  # Duration in seconds that classifies a button press as a medium press
LONG_PRESS = 3  # Duration in seconds that classifies a button press as a long press

# Covers
COVER_DELAY_BEFORE_STOP = 1 # Delay (in seconds) before sending the stop command when the cover is fully open or closed.

# Listener
BUTTON_COMMAND_PREFIX = '#N' # Button pressed prefix
IGNORE_ANSWER = '$0E' # ***Unkwown***
FEEDBACK_REFRESH_COMMAND = ('$1012', '$1017') # Receiving a refresh command initiated from the feedback module
FEEDBACK_MODULE_ANSWER = '$1C' # Receiving refresh command result answering the feeback module request
MANUAL_REFRESH_COMMAND = ('$0512', '$0517') # Receiving refresh command result answering an integration refresh command
COMMAND_PROCESSED = ('$0515', '$0516') # Confirms the command has been received and executed
CONTROLLER_ADDRESS = '$18' # Prefix the Nikobus PC-Link address following an '#A' request

# Command
COMMAND_EXECUTION_DELAY = 0.7  # Delay between command executions in seconds
COMMAND_ACK_WAIT_TIMEOUT = 15  # Timeout for waiting for command ACK in seconds
COMMAND_ANSWER_WAIT_TIMEOUT = 5  # Timeout for waiting for command answer in each loop
MAX_ATTEMPTS = 3  # Maximum attempts for sending commands and waiting for an answer
