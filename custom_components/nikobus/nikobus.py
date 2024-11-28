import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Switches
# state = nikobus_api.get_switch_state(address, channel)
# await nikobus_api.turn_on_switch(address, channel)
# await nikobus_api.turn_off_switch(address, channel)

# Lights
# brightness = nikobus_api.get_light_brightness(address, channel)
# await nikobus_api.turn_on_light(address, channel, brightness)
# await nikobus_api.turn_off_light(address, channel)

# Covers
# state = nikobus_api.get_cover_state(address, channel)
# await nikobus_api.open_cover(address, channel)
# await nikobus_api.close_cover(address, channel)
# await nikobus_api.stop_cover(address, channel, direction)


class NikobusAPI:
    def __init__(self, hass, coordinator):
        """Initialize the Nikobus API class with Home Assistant and the coordinator."""
        self._hass = hass
        self._coordinator = coordinator

    #### SWITCHES
    async def turn_on_switch(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn on a switch specified by its address and channel."""
        self._coordinator.set_bytearray_state(address, channel, 0xFF)
        channel_info = self._coordinator.dict_module_data["switch_module"][address]["channels"][channel - 1]
        led_on = channel_info.get("led_on")

        if led_on:
            await self._coordinator.nikobus_command_handler.queue_command(
                f"#N{led_on}\r#E1", completion_handler=completion_handler
            )
        else:
            await self._coordinator.nikobus_command_handler.set_output_state(
                address, channel, 0xFF, completion_handler=completion_handler
            )

    async def turn_off_switch(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn off a switch specified by its address and channel."""
        self._coordinator.set_bytearray_state(address, channel, 0x00)
        channel_info = self._coordinator.dict_module_data["switch_module"][address]["channels"][channel - 1]
        led_off = channel_info.get("led_off")

        if led_off:
            await self._coordinator.nikobus_command_handler.queue_command(
                f"#N{led_off}\r#E1", completion_handler=completion_handler
            )
        else:
            await self._coordinator.nikobus_command_handler.set_output_state(
                address, channel, 0x00, completion_handler=completion_handler
            )

    #### DIMMERS
    async def turn_on_light(
        self, address: str, channel: int, brightness: int, completion_handler=None
    ) -> None:
        """Turn on a light specified by its address and channel with the given brightness."""
        current_brightness = self._coordinator.get_light_brightness(address, channel)

        # Only turn on the feedback LED if the light is currently off (brightness == 0)
        if current_brightness == 0:
            channel_info = self._coordinator.dict_module_data["dimmer_module"][address]["channels"][channel - 1]
            led_on = channel_info.get("led_on")
            if led_on:
                await self._coordinator.nikobus_command_handler.queue_command(
                    f"#N{led_on}\r#E1", completion_handler=completion_handler
                )

        # Set the new brightness and light state
        self._coordinator.set_bytearray_state(address, channel, brightness)
        await self._coordinator.nikobus_command_handler.set_output_state(
            address, channel, brightness, completion_handler=completion_handler
        )

    async def turn_off_light(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn off a light specified by its address and channel."""
        current_brightness = self._coordinator.get_light_brightness(address, channel)

        # Only turn off the feedback LED if the light is currently on (brightness != 0)
        if current_brightness != 0:
            channel_info = self._coordinator.dict_module_data["dimmer_module"][address]["channels"][channel - 1]
            led_off = channel_info.get("led_off")
            if led_off:
                await self._coordinator.nikobus_command_handler.queue_command(
                    f"#N{led_off}\r#E1", completion_handler=completion_handler
                )

        # Set the light state to off (brightness = 0)
        self._coordinator.set_bytearray_state(address, channel, 0x00)
        await self._coordinator.nikobus_command_handler.set_output_state(
            address, channel, 0x00, completion_handler=completion_handler
        )

    #### COVERS
    async def stop_cover(
        self, address: str, channel: int, direction: str, completion_handler=None
    ) -> None:
        """Stop a cover specified by its address and channel."""
        self._coordinator.set_bytearray_state(address, channel, 0x00)

        channel_data = self._coordinator.dict_module_data["roller_module"][address]["channels"][channel - 1]
        led_on = channel_data.get("led_on")
        led_off = channel_data.get("led_off")
        command = None

        if led_on and direction == "opening":
            command = f"#N{led_on}\r#E1"
        elif led_off and direction == "closing":
            command = f"#N{led_off}\r#E1"

        if command:
            await self._coordinator.nikobus_command_handler.queue_command(
                command, completion_handler=completion_handler
            )
        else:
            await self._coordinator.nikobus_command_handler.set_output_state(
                address, channel, 0x00, completion_handler=completion_handler
            )

    async def open_cover(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Open a cover specified by its address and channel."""
        self._coordinator.set_bytearray_state(address, channel, 0x01)

        channel_data = self._coordinator.dict_module_data["roller_module"][address]["channels"][channel - 1]
        led_on = channel_data.get("led_on")

        if led_on:
            await self._coordinator.nikobus_command_handler.queue_command(
                f"#N{led_on}\r#E1", completion_handler=completion_handler
            )
        else:
            await self._coordinator.nikobus_command_handler.set_output_state(
                address, channel, 0x01, completion_handler=completion_handler
            )

    async def close_cover(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Close a cover specified by its address and channel."""
        self._coordinator.set_bytearray_state(address, channel, 0x02)

        channel_data = self._coordinator.dict_module_data["roller_module"][address]["channels"][channel - 1]
        led_off = channel_data.get("led_off")

        if led_off:
            await self._coordinator.nikobus_command_handler.queue_command(
                f"#N{led_off}\r#E1", completion_handler=completion_handler
            )
        else:
            await self._coordinator.nikobus_command_handler.set_output_state(
                address, channel, 0x02, completion_handler=completion_handler
            )


class NikobusConnectionError(Exception):
    """Custom exception for handling Nikobus connection errors."""
    pass


class NikobusDataError(Exception):
    """Custom exception for handling Nikobus data errors."""
    pass
