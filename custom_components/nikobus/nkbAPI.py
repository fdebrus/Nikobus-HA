import logging
from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)


class NikobusAPI:
    def __init__(self, coordinator):
        """Initialize the Nikobus API class with the coordinator."""
        self._coordinator = coordinator

    #### SWITCHES
    async def turn_on_switch(self, address: str, channel: int, completion_handler=None) -> None:
        """Turn on a switch specified by its address and channel."""
        try:
            channel_info = self._coordinator.dict_module_data["switch_module"][address]["channels"][channel - 1]
            led_on = channel_info.get("led_on")

            if led_on:
                await self._coordinator.nikobus_command_handler.queue_command(f"#N{led_on}\r#E1")
            else:
                await self._coordinator.nikobus_command_handler.set_output_state(address, channel, 0xFF, completion_handler=completion_handler)

            self._coordinator.set_bytearray_state(address, channel, 0xFF)
        except NikobusError as e:
            _LOGGER.error(f"Failed to turn on switch at {address}, channel {channel}: {e}")
            raise

    async def turn_off_switch(self, address: str, channel: int, completion_handler=None) -> None:
        """Turn off a switch specified by its address and channel."""
        try:
            channel_info = self._coordinator.dict_module_data["switch_module"][address]["channels"][channel - 1]
            led_off = channel_info.get("led_off")

            if led_off:
                await self._coordinator.nikobus_command_handler.queue_command(f"#N{led_off}\r#E1")
            else:
                await self._coordinator.nikobus_command_handler.set_output_state(address, channel, 0x00, completion_handler=completion_handler)

            self._coordinator.set_bytearray_state(address, channel, 0x00)
        except NikobusError as e:
            _LOGGER.error(f"Failed to turn off switch at {address}, channel {channel}: {e}")
            raise

    #### DIMMERS
    async def turn_on_light(self, address: str, channel: int, brightness: int, completion_handler=None) -> None:
        """Turn on a light specified by its address and channel with the given brightness."""
        try:
            current_brightness = self._coordinator.get_light_brightness(address, channel)

            if current_brightness == 0:
                channel_info = self._coordinator.dict_module_data["dimmer_module"][address]["channels"][channel - 1]
                led_on = channel_info.get("led_on")
                if led_on:
                    await self._coordinator.nikobus_command_handler.queue_command(f"#N{led_on}\r#E1")

            await self._coordinator.nikobus_command_handler.set_output_state(address, channel, brightness, completion_handler=completion_handler)
            self._coordinator.set_bytearray_state(address, channel, brightness)
        except NikobusError as e:
            _LOGGER.error(f"Failed to turn on light at {address}, channel {channel}: {e}")
            raise

    async def turn_off_light(self, address: str, channel: int, completion_handler=None) -> None:
        """Turn off a light specified by its address and channel."""
        try:
            current_brightness = self._coordinator.get_light_brightness(address, channel)

            if current_brightness != 0:
                channel_info = self._coordinator.dict_module_data["dimmer_module"][address]["channels"][channel - 1]
                led_off = channel_info.get("led_off")
                if led_off:
                    await self._coordinator.nikobus_command_handler.queue_command(f"#N{led_off}\r#E1")

            await self._coordinator.nikobus_command_handler.set_output_state(address, channel, 0x00, completion_handler=completion_handler)
            self._coordinator.set_bytearray_state(address, channel, 0x00)
        except NikobusError as e:
            _LOGGER.error(f"Failed to turn off light at {address}, channel {channel}: {e}")
            raise

    #### COVERS
    async def stop_cover(self, address: str, channel: int, direction: str, completion_handler=None) -> None:
        """Stop a cover specified by its address and channel."""
        try:
            channel_data = self._coordinator.dict_module_data["roller_module"][address]["channels"][channel - 1]
            led_on = channel_data.get("led_on")
            led_off = channel_data.get("led_off")
            command = None

            if led_on and direction == "opening":
                command = f"#N{led_on}\r#E1"
            elif led_off and direction == "closing":
                command = f"#N{led_off}\r#E1"

            if command:
                await self._coordinator.nikobus_command_handler.queue_command(command)
            else:
                await self._coordinator.nikobus_command_handler.set_output_state(address, channel, 0x00, completion_handler=completion_handler)

            self._coordinator.set_bytearray_state(address, channel, 0x00)
        except NikobusError as e:
            _LOGGER.error(f"Failed to stop cover at {address}, channel {channel}: {e}")
            raise

    async def open_cover(self, address: str, channel: int, completion_handler=None) -> None:
        """Open a cover specified by its address and channel."""
        try:
            channel_data = self._coordinator.dict_module_data["roller_module"][address]["channels"][channel - 1]
            led_on = channel_data.get("led_on")

            if led_on:
                await self._coordinator.nikobus_command_handler.queue_command(f"#N{led_on}\r#E1")
            else:
                await self._coordinator.nikobus_command_handler.set_output_state(address, channel, 0x01, completion_handler=completion_handler)

            self._coordinator.set_bytearray_state(address, channel, 0x01)
        except NikobusError as e:
            _LOGGER.error(f"Failed to open cover at {address}, channel {channel}: {e}")
            raise

    async def close_cover(self, address: str, channel: int, completion_handler=None) -> None:
        """Close a cover specified by its address and channel."""
        try:
            channel_data = self._coordinator.dict_module_data["roller_module"][address]["channels"][channel - 1]
            led_off = channel_data.get("led_off")

            if led_off:
                await self._coordinator.nikobus_command_handler.queue_command(f"#N{led_off}\r#E1")
            else:
                await self._coordinator.nikobus_command_handler.set_output_state(address, channel, 0x02, completion_handler=completion_handler)

            self._coordinator.set_bytearray_state(address, channel, 0x02)
        except NikobusError as e:
            _LOGGER.error(f"Failed to close cover at {address}, channel {channel}: {e}")
            raise

    async def set_output_states_for_module(self, address: str, completion_handler=None) -> None:
        """Set the output states for a module with multiple channel updates at once."""
        try:
            _LOGGER.debug(f"Setting output states for module {address}")
            await self._coordinator.nikobus_command_handler.set_output_states(address, completion_handler=completion_handler)
        except NikobusError as e:
            _LOGGER.error(f"Failed to set output states for module {address}: {e}")
            raise
