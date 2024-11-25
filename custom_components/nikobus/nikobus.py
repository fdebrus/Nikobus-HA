"""API for Nikobus"""

import logging

from .const import DOMAIN

from .nkbconnect import NikobusConnect
from .nkbconfig import NikobusConfig
from .nkblistener import NikobusEventListener
from .nkbcommand import NikobusCommandHandler
from .nkbactuator import NikobusActuator

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


class Nikobus:
    def __init__(
        self, hass, config_entry: ConfigEntry, connection_string, async_event_handler
    ):
        self._hass = hass
        self._config_entry = config_entry
        self._async_event_handler = async_event_handler
        self._controller_address = None
        self._nikobus_module_states = {}
        self._nikobus_connection = NikobusConnect(connection_string)
        self._nikobus_config = NikobusConfig(self._hass)

        self.dict_module_data = {}
        self.dict_button_data = {}
        self.dict_scene_data = {}
        self._nikobus_actuator = None
        self._nikobus_listener = None

        self.nikobus_command_handler = None

    @classmethod
    async def create(cls, hass, config_entry, connection_string, async_event_handler):
        _LOGGER.debug(
            f"Creating Nikobus instance with connection string: {connection_string}"
        )
        instance = cls(hass, config_entry, connection_string, async_event_handler)
        if await instance.connect():
            _LOGGER.info("Nikobus instance created and connected successfully")
            return instance
        _LOGGER.error("Failed to create Nikobus instance")
        return None

    #### CONNECT TO NIKOBUS
    async def connect(self) -> bool:
        if await self._nikobus_connection.connect():
            try:
                self.dict_module_data = await self._nikobus_config.load_json_data(
                    "nikobus_module_config.json", "module"
                )
                self.dict_button_data = await self._nikobus_config.load_json_data(
                    "nikobus_button_config.json", "button"
                )
                self.dict_scene_data = await self._nikobus_config.load_json_data(
                    "nikobus_scene_config.json", "scene"
                )

                for module_type, modules in self.dict_module_data.items():
                    for address, module_info in modules.items():
                        module_address = module_info["address"]
                        self._nikobus_module_states[module_address] = bytearray(12)

                self._nikobus_actuator = NikobusActuator(
                    self._hass,
                    self.dict_button_data,
                    self.dict_module_data,
                    self._async_event_handler,
                )
                self._hass.data[DOMAIN]["nikobus_actuator"] = self._nikobus_actuator

                self._nikobus_listener = NikobusEventListener(
                    self._hass,
                    self._config_entry,
                    self._nikobus_actuator,
                    self._nikobus_connection,
                    self.process_feedback_data,
                )
                self._hass.data[DOMAIN]["nikobus_listener"] = self._nikobus_listener

                self.nikobus_command_handler = NikobusCommandHandler(
                    self._hass,
                    self._nikobus_connection,
                    self._nikobus_listener,
                    self._nikobus_module_states,
                )
                self._hass.data[DOMAIN]["nikobus_command_handler"] = (
                    self.nikobus_command_handler
                )

                return True
            except HomeAssistantError as e:
                raise HomeAssistantError(
                    f"An error occurred loading configuration files: {e}"
                )
        return False

    #### EVENT AND COMMAND LOOPS
    async def listen_for_events(self):
        await self._nikobus_listener.start()

    async def command_handler(self):
        await self.nikobus_command_handler.start()

    #### REFRESH DATA FROM NIKOBUS
    async def refresh_nikobus_data(self) -> bool:
        if "switch_module" in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data["switch_module"])

        if "dimmer_module" in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data["dimmer_module"])

        if "roller_module" in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data["roller_module"])

        return True

    async def _refresh_module_type(self, modules_dict):
        for address, module_data in modules_dict.items():
            _LOGGER.debug(f"Refreshing data for module address: {address}")
            state = ""
            channel_count = len(module_data.get("channels", []))
            groups_to_query = [1] if channel_count <= 6 else [1, 2]

            for group in groups_to_query:
                group_state = (
                    await self.nikobus_command_handler.get_output_state(address, group)
                    or ""
                )
                _LOGGER.debug(
                    f"State for group {group}: {group_state} address : {address} ***"
                )
                state += group_state

            self._nikobus_module_states[address] = bytearray.fromhex(state)
            _LOGGER.debug(f"{self._nikobus_module_states[address]}")

    async def process_feedback_data(self, module_group, data):
        """Process feedback data from Nikobus"""
        try:
            module_address_raw = data[3:7]
            module_address = module_address_raw[2:] + module_address_raw[:2]

            module_state_raw = data[9:21]

            _LOGGER.debug(
                f"Processing feedback module data: module_address={module_address}, group={module_group}, module_state={module_state_raw}"
            )

            if module_address not in self._nikobus_module_states:
                self._nikobus_module_states[module_address] = bytearray(12)

            if module_group == 1:
                self._nikobus_module_states[module_address][:6] = bytearray.fromhex(
                    module_state_raw
                )
            elif module_group == 2:
                self._nikobus_module_states[module_address][6:] = bytearray.fromhex(
                    module_state_raw
                )
            else:
                raise ValueError(f"Invalid module group: {module_group}")

            await self._async_event_handler(
                "nikobus_refreshed", {"impacted_module_address": module_address}
            )

        except Exception as e:
            _LOGGER.error(f"Error processing feedback data: {e}", exc_info=True)

    #### UTILS
    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Get the state of a specific channel"""
        return self._nikobus_module_states.get(address, bytearray())[channel - 1]

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of a specific channel"""
        if address in self._nikobus_module_states:
            self._nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.error(f"Address {address} not found in Nikobus module")

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update the state of a specific group"""
        byte_value = bytearray.fromhex(value)
        if address in self._nikobus_module_states:
            if int(group) == 1:
                self._nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self._nikobus_module_states[address][6:12] = byte_value
            _LOGGER.debug(
                f"New value set for array {self._nikobus_module_states[address]}."
            )
        else:
            _LOGGER.error(f"Address {address} not found in Nikobus module")

    def get_module_type(self, module_id: str) -> str:
        """Determine the module type based on the module ID."""
        # Check in switch modules
        if "switch_module" in self.dict_module_data:
            if module_id in self.dict_module_data["switch_module"]:
                return "switch"
        # Check in dimmer modules
        if "dimmer_module" in self.dict_module_data:
            if module_id in self.dict_module_data["dimmer_module"]:
                return "dimmer"
        # Check in cover/roller modules
        if "roller_module" in self.dict_module_data:
            if module_id in self.dict_module_data["roller_module"]:
                return "cover"
        # If not found, return unknown
        _LOGGER.error(f"Module ID {module_id} not found in known module types")
        return "unknown"

    #### SCENES
    async def set_output_states_for_module(
        self, address: str, channel_states: bytearray, completion_handler=None
    ) -> None:
        """Set the output states for a module with multiple channel updates at once."""
        _LOGGER.debug(
            f"Setting output states for module {address}: {channel_states.hex()}"
        )
        await self.nikobus_command_handler.set_output_states(
            address, channel_states, completion_handler=completion_handler
        )

    #### SWITCHES
    def get_switch_state(self, address: str, channel: int) -> bool:
        """Get the state of a switch based on its address and channel."""
        return self.get_bytearray_state(address, channel) == 0xFF

    async def turn_on_switch(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn on a switch specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0xFF)
        channel_info = self.dict_module_data["switch_module"][address]["channels"][
            channel - 1
        ]
        led_on = channel_info.get("led_on")

        if led_on:
            await self.nikobus_command_handler.queue_command(
                f"#N{led_on}\r#E1", address, channel, completion_handler=completion_handler
            )
        else:
            await self.nikobus_command_handler.set_output_state(
                address, channel, 0xFF, completion_handler=completion_handler
            )

    async def turn_off_switch(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn off a switch specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x00)
        channel_info = self.dict_module_data["switch_module"][address]["channels"][
            channel - 1
        ]
        led_off = channel_info.get("led_off")

        if led_off:
            await self.nikobus_command_handler.queue_command(
                f"#N{led_off}\r#E1", address, channel, completion_handler=completion_handler
            )
        else:
            await self.nikobus_command_handler.set_output_state(
                address, channel, 0x00, completion_handler=completion_handler
            )

    #### DIMMERS
    def get_light_state(self, address: str, channel: int) -> bool:
        """Get the state of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel) != 0x00

    def get_light_brightness(self, address: str, channel: int) -> int:
        """Get the brightness of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    async def turn_on_light(
        self, address: str, channel: int, brightness: int, completion_handler=None
    ) -> None:
        """Turn on a light specified by its address and channel with the given brightness."""
        current_brightness = self.get_light_brightness(address, channel)

        # Only turn on the feedback LED if the light is currently off (brightness == 0)
        if current_brightness == 0:
            channel_info = self.dict_module_data["dimmer_module"][address]["channels"][
                channel - 1
            ]
            led_on = channel_info.get("led_on")
            if led_on:
                await self.nikobus_command_handler.queue_command(
                    f"#N{led_on}\r#E1", address, channel, completion_handler=completion_handler
                )

        # Set the new brightness and light state
        self.set_bytearray_state(address, channel, brightness)
        await self.nikobus_command_handler.set_output_state(
            address, channel, brightness, completion_handler=completion_handler
        )

    async def turn_off_light(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn off a light specified by its address and channel."""
        current_brightness = self.get_light_brightness(address, channel)

        # Only turn off the feedback LED if the light is currently on (brightness != 0)
        if current_brightness != 0:
            channel_info = self.dict_module_data["dimmer_module"][address]["channels"][
                channel - 1
            ]
            led_off = channel_info.get("led_off")
            if led_off:
                await self.nikobus_command_handler.queue_command(
                    f"#N{led_off}\r#E1", address, channel, completion_handler=completion_handler
                )

        # Set the light state to off (brightness = 0)
        self.set_bytearray_state(address, channel, 0x00)
        await self.nikobus_command_handler.set_output_state(
            address, channel, 0x00, completion_handler=completion_handler
        )

    #### COVERS
    def get_cover_state(self, address: str, channel: int) -> int:
        """Get the state of a cover based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    async def stop_cover(
        self, address: str, channel: int, direction: str, completion_handler=None
    ) -> None:
        """Stop a cover specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x00)

        channel_data = self.dict_module_data["roller_module"][address]["channels"][
            channel - 1
        ]
        led_on = channel_data.get("led_on")
        led_off = channel_data.get("led_off")
        command = None

        if led_on and direction == "opening":
            command = f"#N{led_on}\r#E1"
        elif led_off and direction == "closing":
            command = f"#N{led_off}\r#E1"

        if command:
            await self.nikobus_command_handler.queue_command(
                command, address, channel, completion_handler=completion_handler
            )
        else:
            await self.nikobus_command_handler.set_output_state(
                address, channel, 0x00, completion_handler=completion_handler
            )

    async def open_cover(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Open a cover specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x01)

        channel_data = self.dict_module_data["roller_module"][address]["channels"][
            channel - 1
        ]
        led_on = channel_data.get("led_on")

        if led_on:
            await self.nikobus_command_handler.queue_command(
                f"#N{led_on}\r#E1", address, channel, completion_handler=completion_handler
            )
        else:
            await self.nikobus_command_handler.set_output_state(
                address, channel, 0x01, completion_handler=completion_handler
            )

    async def close_cover(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Close a cover specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x02)

        channel_data = self.dict_module_data["roller_module"][address]["channels"][
            channel - 1
        ]
        led_off = channel_data.get("led_off")

        if led_off:
            await self.nikobus_command_handler.queue_command(
                f"#N{led_off}\r#E1", address, channel, completion_handler=completion_handler
            )
        else:
            await self.nikobus_command_handler.set_output_state(
                address, channel, 0x02, completion_handler=completion_handler
            )


class NikobusConnectionError(Exception):
    pass


class NikobusDataError(Exception):
    pass
