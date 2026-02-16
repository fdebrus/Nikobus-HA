"""Optimized Nikobus API for Controlling Switches, Lights, and Covers."""

from __future__ import annotations

import logging
from typing import Any, Callable

from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)

# Nikobus state constants for clarity
STATE_OFF = 0x00
STATE_ON = 0xFF
STATE_OPEN = 0x01
STATE_CLOSE = 0x02

class NikobusAPI:
    """Refined Nikobus API with optimistic state updates and consolidated logic."""

    def __init__(self, hass: Any, coordinator: Any) -> None:
        """Initialize the API."""
        self._hass = hass
        self._coordinator = coordinator

    def _get_channel_info(self, module_key: str, address: str, channel: int) -> dict | None:
        """Safely retrieve channel metadata."""
        module_data = self._coordinator.dict_module_data.get(module_key, {})
        try:
            return module_data.get(address, {}).get("channels", [])[channel - 1]
        except (IndexError, KeyError):
            return None

    async def _send_bus_command(self, bus_addr: str, completion_handler: Callable | None = None) -> None:
        """Helper to send a standard Nikobus bus trigger (#N...#E1)."""
        await self._coordinator.nikobus_command.queue_command(
            f"#N{bus_addr}\r#E1", completion_handler=completion_handler
        )

    async def _dispatch_action(
        self, 
        module_key: str, 
        address: str, 
        channel: int, 
        target_state: int, 
        cmd_key: str, 
        completion_handler: Callable | None = None
    ) -> None:
        """
        Unified dispatcher for all module actions.
        Implements optimistic updates for instant UI response.
        """
        self._coordinator.set_bytearray_state(address, channel, target_state)
        chan_info = self._get_channel_info(module_key, address, channel)
        bus_cmd = chan_info.get(cmd_key) if chan_info else None

        try:
            if bus_cmd:
                _LOGGER.debug("Sending bus trigger for %s: %s", address, bus_cmd)
                await self._send_bus_command(bus_cmd, completion_handler)
            else:
                _LOGGER.debug("Setting output state for %s chan %d to %s", address, channel, hex(target_state))
                await self._coordinator.nikobus_command.set_output_state(
                    address, channel, target_state, completion_handler=completion_handler
                )
        except NikobusError as err:
            _LOGGER.error("API Action failed for %s: %s", address, err)
            raise

    #### SWITCHES
    async def turn_on_switch(self, address: str, channel: int, completion_handler: Callable | None = None) -> None:
        """Turn on a switch module output."""
        await self._dispatch_action("switch_module", address, channel, STATE_ON, "led_on", completion_handler)

    async def turn_off_switch(self, address: str, channel: int, completion_handler: Callable | None = None) -> None:
        """Turn off a switch module output."""
        await self._dispatch_action("switch_module", address, channel, STATE_OFF, "led_off", completion_handler)

    #### DIMMERS
    async def turn_on_light(self, address: str, channel: int, brightness: int, completion_handler: Callable | None = None) -> None:
        """Turn on a dimmer output to a specific brightness."""
        # For dimmers, we often send the bus trigger (led_on) first to wake the module, then the brightness
        self._coordinator.set_bytearray_state(address, channel, brightness)
        chan_info = self._get_channel_info("dimmer_module", address, channel)
        
        if brightness > 0 and (led_on := chan_info.get("led_on") if chan_info else None):
             await self._send_bus_command(led_on)

        await self._coordinator.nikobus_command.set_output_state(
            address, channel, brightness, completion_handler=completion_handler
        )

    async def turn_off_light(self, address: str, channel: int, completion_handler: Callable | None = None) -> None:
        """Turn off a dimmer output."""
        self._coordinator.set_bytearray_state(address, channel, STATE_OFF)
        chan_info = self._get_channel_info("dimmer_module", address, channel)
        
        if led_off := chan_info.get("led_off") if chan_info else None:
            await self._send_bus_command(led_off)

        await self._coordinator.nikobus_command.set_output_state(
            address, channel, STATE_OFF, completion_handler=completion_handler
        )

    #### COVERS
    async def open_cover(self, address: str, channel: int, completion_handler: Callable | None = None) -> None:
        """Open a cover/roller shutter."""
        await self._dispatch_action("roller_module", address, channel, STATE_OPEN, "led_on", completion_handler)

    async def close_cover(self, address: str, channel: int, completion_handler: Callable | None = None) -> None:
        """Close a cover/roller shutter."""
        await self._dispatch_action("roller_module", address, channel, STATE_CLOSE, "led_off", completion_handler)

    async def stop_cover(self, address: str, channel: int, direction: str, completion_handler: Callable | None = None) -> None:
        """Stop cover movement."""
        self._coordinator.set_bytearray_state(address, channel, STATE_OFF)
        chan_info = self._get_channel_info("roller_module", address, channel)
        
        # Decide which stop command to send based on current direction
        cmd_key = "led_on" if direction == "opening" else "led_off"
        bus_cmd = chan_info.get(cmd_key) if chan_info else None

        if bus_cmd:
            await self._send_bus_command(bus_cmd, completion_handler)
        else:
            await self._coordinator.nikobus_command.set_output_state(address, channel, STATE_OFF, completion_handler)

    async def set_output_states_for_module(self, address: str, completion_handler: Callable | None = None) -> None:
        """Batch update all output states for a specific module."""
        await self._coordinator.nikobus_command.set_output_states(address, completion_handler=completion_handler)