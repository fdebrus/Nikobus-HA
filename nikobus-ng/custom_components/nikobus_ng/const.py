"""Constants of the Nikobus NG integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "nikobus_ng"
MANUFACTURER: Final = "Niko"

CONF_CONNECTION: Final = "connection"
CONF_FEEDBACK_MODULE: Final = "feedback_module"
#: Relay outputs shown as lights, each ``"<module>:<channel>"``.
CONF_LIGHT_OUTPUTS: Final = "light_outputs"
#: How many times a press from Home Assistant repeats its telegram, as a held key does.
CONF_PRESS_REPEAT: Final = "press_repeat"
DEFAULT_PRESS_REPEAT: Final = 3
#: A PC-Link older than generation 3: no read before a write, no periodic read.
CONF_OLD_PC_LINK: Final = "old_pc_link"
#: Per module address: the type to treat it as, when the inventory has it wrong.
CONF_MODULE_TYPES: Final = "module_types"
#: Dimmer outputs shown as fans, each ``"<module>:<channel>"``.
CONF_FAN_OUTPUTS: Final = "fan_outputs"
#: Per output ``"<module>:<channel>"``: the keys pressed to switch it, so plate LEDs follow.
CONF_LED_KEYS: Final = "led_keys"
CONF_ON_KEY: Final = "on_key"
CONF_OFF_KEY: Final = "off_key"
#: Per roller output ``"<module>:<channel>"``: what it drives and its run times.
CONF_SHUTTERS: Final = "shutters"
CONF_OUTPUT: Final = "output"
CONF_USE_AS: Final = "use_as"
CONF_OPEN_TIME: Final = "open_time"
CONF_CLOSE_TIME: Final = "close_time"
CONF_END_MARGIN: Final = "end_margin"
USE_AS_COVER: Final = "cover"
USE_AS_SWITCH: Final = "switch"
USE_AS_LIGHT: Final = "light"
USE_AS: Final = (USE_AS_COVER, USE_AS_SWITCH, USE_AS_LIGHT)

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.EVENT,
    Platform.FAN,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SWITCH,
]

#: Module types whose outputs are read and written.
SWITCH_MODULE: Final = "switch_module"
DIMMER_MODULE: Final = "dimmer_module"
ROLLER_MODULE: Final = "roller_module"
OUTPUT_MODULE_TYPES: Final = (SWITCH_MODULE, DIMMER_MODULE, ROLLER_MODULE)
#: How many outputs a module of each type can have; the first is the usual one.
MODULE_CHANNELS: Final = {SWITCH_MODULE: (12, 4), DIMMER_MODULE: (12, 4), ROLLER_MODULE: (6,)}
#: The colour controller: read like an output module, driven by its keys only.
RGB_MODULE: Final = "rgb_module"
#: Module types whose inputs the discovery files as plates of their own.
INPUT_MODULE_TYPES: Final = ("pc_logic", "interface_module")

#: Every output group is read this often when nothing pushes its state.
POLL_INTERVAL: Final = timedelta(seconds=120)
#: With a Feedback Module the groups it reports are left alone; a group
#: silent for this long is read.
FEEDBACK_SAFETY_POLL: Final = timedelta(seconds=300)

#: A relay has switched this long after the first frame of a press.
PRESS_READ_DELAY: Final = 0.3
#: A relay group is read again this long after the key is released.
RELEASE_READ_DELAY: Final = 0.2
#: A dimmer is still moving when the key is released.
DIMMER_READ_DELAY: Final = 1.0
#: How often a held key is checked for its release.
RELEASE_POLL: Final = 0.05

RECONNECT_DELAY_INITIAL: Final = 5.0
RECONNECT_DELAY_MAX: Final = 60.0

#: A PC-Link answers the inventory within this long, or there is none.
INVENTORY_PROBE_TIMEOUT: Final = 8.0
#: The library closes an unanswered inventory by itself within this long.
INVENTORY_CLOSE_TIMEOUT: Final = 15.0

#: Roller output values, as the module reports and accepts them.
COVER_STOPPED: Final = 0x00
COVER_OPENING: Final = 0x01
COVER_CLOSING: Final = 0x02
#: A shutter with no run time in its links takes this long.
COVER_DEFAULT_RUN_TIME: Final = 30.0
#: The motor is left running this long past a fully open or closed
#: estimate, so the shutter reaches its end stop.
COVER_END_MARGIN: Final = 3.0

#: The PC-Link's clock is read this often.
CLOCK_READ_INTERVAL: Final = timedelta(hours=1)
#: The modules' checksums are compared this long after a start, then daily.
REPROGRAMMED_CHECK_DELAY: Final = timedelta(minutes=10)
REPROGRAMMED_CHECK_INTERVAL: Final = timedelta(days=1)

#: A module silent for this many reads in a row, while others answer, is reported.
SILENT_READS: Final = 3
#: Such a module is then asked only this often: each read of it is a timeout on the bus.
SILENT_RETRY: Final = timedelta(hours=1)
ISSUE_NOT_ANSWERING: Final = "module_not_answering"
ISSUE_CORRUPT_LINKS: Final = "module_corrupt_links"
ISSUE_NO_DEVICE: Final = "no_device_answered"
ISSUE_FEEDBACK_PORT: Final = "feedback_module_port"
ISSUE_TYPE_FIXABLE: Final = "module_type_fixable"
#: This many pushed states with none of the queries behind them: the port is the Feedback Module's own.
FEEDBACK_PORT_ANSWERS: Final = 6

#: Fired for every key press seen on the bus, known key or not.
EVENT_PRESS: Final = "nikobus_ng_press"
