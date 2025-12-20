"""Nikobus API for Controlling Switches, Lights, and Covers."""

import logging
from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)


class NikobusAPI:
    """Nikobus API for controlling switches, dimmers, and covers."""

    def __init__(self, hass, coordinator):
        """Initialize the Nikobus API class with Home Assistant and the coordinator."""
        self._hass = hass
        self._coordinator = coordinator

    def _get_channel_info(
        self, module_key: str, address: str, channel: int
    ) -> dict | None:
        """Retrieve channel information safely from module data."""
        channels = (
            self._coordinator.dict_module_data.get(module_key, {})
            .get(address, {})
            .get("channels", [])
        )

        if 0 < channel <= len(channels):
            return channels[channel - 1]

        _LOGGER.warning(
            "Channel %d not found for %s in module %s", channel, address, module_key
        )
        return None

    async def _queue_led_command(self, command: str, completion_handler=None) -> None:
        """Queue a LED command if one is provided."""
        if not command:
            return

        _LOGGER.debug("Sending LED command '%s'", command.strip())
        await self._coordinator.nikobus_command.queue_command(
            f"#N{command}\r#E1", completion_handler=completion_handler
        )

    async def _execute_command(
        self,
        address: str,
        channel: int,
        command: str,
        state: int,
        completion_handler=None,
    ) -> None:
        """Execute a LED command if available; otherwise, set the output state."""
        if command:
            await self._queue_led_command(command, completion_handler)
        else:
            await self._coordinator.nikobus_command.set_output_state(
                address, channel, state, completion_handler=completion_handler
            )
        self._coordinator.set_bytearray_state(address, channel, state)

    #### SWITCHES
    async def turn_on_switch(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn on a switch specified by its address and channel."""
        channel_info = self._get_channel_info("switch_module", address, channel)
        led_on = channel_info.get("led_on") if channel_info else None

        try:
            await self._execute_command(
                address, channel, led_on, 0xFF, completion_handler
            )
        except NikobusError as e:
            _LOGGER.error(
                "Failed to turn on switch at %s, channel %d: %s", address, channel, e
            )
            raise

    async def turn_off_switch(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn off a switch specified by its address and channel."""
        channel_info = self._get_channel_info("switch_module", address, channel)
        led_off = channel_info.get("led_off") if channel_info else None

        try:
            await self._execute_command(
                address, channel, led_off, 0x00, completion_handler
            )
        except NikobusError as e:
            _LOGGER.error(
                "Failed to turn off switch at %s, channel %d: %s", address, channel, e
            )
            raise

    #### DIMMERS
    async def turn_on_light(
        self, address: str, channel: int, brightness: int, completion_handler=None
    ) -> None:
        """Turn on a light specified by its address and channel with the given brightness."""
        current_brightness = self._coordinator.get_light_brightness(address, channel)
        channel_info = self._get_channel_info("dimmer_module", address, channel)
        led_on = channel_info.get("led_on") if channel_info else None

        try:
            if current_brightness == 0 and led_on:
                await self._queue_led_command(led_on, completion_handler)

            await self._coordinator.nikobus_command.set_output_state(
                address, channel, brightness, completion_handler=completion_handler
            )
            self._coordinator.set_bytearray_state(address, channel, brightness)
        except NikobusError as e:
            _LOGGER.error(
                "Failed to turn on light at %s, channel %d: %s", address, channel, e
            )
            raise

    async def turn_off_light(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Turn off a light specified by its address and channel."""
        current_brightness = self._coordinator.get_light_brightness(address, channel)
        channel_info = self._get_channel_info("dimmer_module", address, channel)
        led_off = channel_info.get("led_off") if channel_info else None

        try:
            if current_brightness != 0 and led_off:
                await self._queue_led_command(led_off, completion_handler)

            await self._coordinator.nikobus_command.set_output_state(
                address, channel, 0x00, completion_handler=completion_handler
            )
            self._coordinator.set_bytearray_state(address, channel, 0x00)
        except NikobusError as e:
            _LOGGER.error(
                "Failed to turn off light at %s, channel %d: %s", address, channel, e
            )
            raise

    #### COVERS
    async def stop_cover(
        self, address: str, channel: int, direction: str, completion_handler=None
    ) -> None:
        """Stop a cover specified by its address and channel."""
        channel_data = self._get_channel_info("roller_module", address, channel)
        led_on = channel_data.get("led_on") if channel_data else None
        led_off = channel_data.get("led_off") if channel_data else None

        command = None
        if led_on and direction == "opening":
            command = led_on
        elif led_off and direction == "closing":
            command = led_off

        try:
            if command:
                _LOGGER.debug(
                    "Sending STOP command for cover at %s, channel %d, direction %s",
                    address,
                    channel,
                    direction,
                )
                await self._coordinator.nikobus_command.queue_command(
                    f"#N{command}\r#E1",
                    completion_handler=completion_handler,
                )
            else:
                await self._coordinator.nikobus_command.set_output_state(
                    address, channel, 0x00, completion_handler=completion_handler
                )
            self._coordinator.set_bytearray_state(address, channel, 0x00)
        except NikobusError as e:
            _LOGGER.error(
                "Failed to stop cover at %s, channel %d: %s", address, channel, e
            )
            raise

    async def open_cover(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Open a cover specified by its address and channel."""
        channel_data = self._get_channel_info("roller_module", address, channel)
        led_on = channel_data.get("led_on") if channel_data else None

        try:
            await self._execute_command(
                address, channel, led_on, 0x01, completion_handler
            )
        except NikobusError as e:
            _LOGGER.error(
                "Failed to open cover at %s, channel %d: %s", address, channel, e
            )
            raise

    async def close_cover(
        self, address: str, channel: int, completion_handler=None
    ) -> None:
        """Close a cover specified by its address and channel."""
        channel_data = self._get_channel_info("roller_module", address, channel)
        led_off = channel_data.get("led_off") if channel_data else None

        try:
            await self._execute_command(
                address, channel, led_off, 0x02, completion_handler
            )
        except NikobusError as e:
            _LOGGER.error(
                "Failed to close cover at %s, channel %d: %s", address, channel, e
            )
            raise

    async def set_output_states_for_module(
        self, address: str, completion_handler=None
    ) -> None:
        """Set the output states for a module with multiple channel updates at once."""
        _LOGGER.debug("Setting output states for module %s", address)
        await self._coordinator.nikobus_command.set_output_states(
            address, completion_handler=completion_handler
        )
