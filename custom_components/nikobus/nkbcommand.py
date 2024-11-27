import re
import asyncio
import logging
from .nkbprotocol import make_pc_link_command, calculate_group_number
from .const import (
    COMMAND_EXECUTION_DELAY,
    COMMAND_ACK_WAIT_TIMEOUT,
    COMMAND_ANSWER_WAIT_TIMEOUT,
    MAX_ATTEMPTS,
)

_LOGGER = logging.getLogger(__name__)

__version__ = "1.0"


class NikobusCommandHandler:
    """Handles command processing for Nikobus."""

    def __init__(
        self, hass, nikobus_connection, nikobus_listener, nikobus_module_states
    ):
        """Initialize the command handler."""
        self._hass = hass
        self._running = False
        self._command_completion_handlers = {}
        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states

    async def start(self):
        """Start the command handler."""
        self._running = True
        _LOGGER.info("NikobusCommandHandler started.")

    async def stop(self):
        """Stop the command handler."""
        self._running = False
        _LOGGER.info("NikobusCommandHandler stopped.")

    async def get_output_state(self, address: str, group: int) -> str:
        """Get the output state of a module."""
        _LOGGER.debug(f"Getting output state - Address: {address}, Group: {group}")
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
        return await self.send_command_get_answer(command, address)

    async def set_output_state(
        self, address: str, channel: int, value: int, completion_handler=None
    ) -> None:
        """Set the output state of a module."""
        _LOGGER.debug(
            f"Setting output state - Address: {address}, Channel: {channel}, Value: {value}"
        )
        group = calculate_group_number(channel)
        command_code = 0x15 if int(group) == 1 else 0x16

        # Get the current state values for the relevant group
        values = await self._prepare_values_for_command(address, group)
        _LOGGER.debug(f"Current values before update: {values}")

        # Calculate the zero-based index for the target channel
        channel_index = (channel - 1) % 6
        # Update the value for the target channel
        values[channel_index] = value
        _LOGGER.debug(f"Updated values: {values}")

        # Create and send the command with the updated values
        command = make_pc_link_command(command_code, address, values)
        await self.queue_command(
            command, address, channel, completion_handler=completion_handler
        )
        _LOGGER.debug("Command queued successfully.")

    async def set_output_states(
        self,
        address: str,
        group: int,
        channel_states: bytearray,
        completion_handler=None,
    ) -> None:
        """Prepare and queue the output states for a module, split by group if necessary."""
        _LOGGER.debug(
            f"Preparing to set output states for module {address}: {channel_states.hex()}"
        )
        command_code = 0x15 if group == 1 else 0x16
        _LOGGER.debug(
            f"Queuing command for Group {group} of module {address}: {channel_states.hex()}"
        )
        command = make_pc_link_command(
            command_code, address, channel_states + bytearray([0xFF])
        )
        # As channel is not available, use group instead
        await self.queue_command(
            command, address, group, completion_handler=completion_handler
        )

    async def queue_command(self, command, address, channel, completion_handler=None):
        """Process the command immediately without locking or queuing."""
        unique_command_key = f"{command}_{address}_{channel}"
        _LOGGER.debug(f"Processing command: {unique_command_key} for module {address}")
        if completion_handler:
            self._command_completion_handlers[unique_command_key] = completion_handler

        withAck = unique_command_key in self._command_completion_handlers
        success = await self.send_command(command, address, withAck=withAck)
        if success:
            _LOGGER.debug(
                f"Command {command} executed successfully for module {address}, channel {channel}"
            )
        else:
            _LOGGER.error(
                f"Command {command} failed for module {address}, channel {channel}"
            )
            # Handle failure if necessary

        handler = self._command_completion_handlers.pop(unique_command_key, None)
        if handler:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(success)
                else:
                    handler(success)
            except Exception as e:
                _LOGGER.error(f"Error executing completion handler: {e}")

        # Optional: Delay before processing the next command
        await asyncio.sleep(COMMAND_EXECUTION_DELAY)

    async def send_command(self, command: str, address: str, withAck: bool) -> bool:
        """Send a command to the Nikobus system."""
        ack_received = False
        answer_received = False
        _LOGGER.debug(f"Sending command: {command}")
        _wait_command_ack, _wait_command_answer = self._prepare_ack_and_answer_signals(
            command, address
        )
        if not withAck:
            await self.nikobus_connection.send(command)
            return True
        else:
            for attempt in range(MAX_ATTEMPTS):
                await self.nikobus_connection.send(command)
                _LOGGER.debug(
                    f"Attempt {attempt + 1} of {MAX_ATTEMPTS} waiting for {_wait_command_ack} / {_wait_command_answer[-4:]}"
                )
                end_time = asyncio.get_event_loop().time() + COMMAND_ACK_WAIT_TIMEOUT
                while asyncio.get_event_loop().time() < end_time:
                    try:
                        message = await asyncio.wait_for(
                            self.nikobus_listener.cmd_response_queue.get(),
                            timeout=COMMAND_ANSWER_WAIT_TIMEOUT,
                        )
                        _LOGGER.debug(f"Message received: {message}")
                        if _wait_command_ack in message:
                            _LOGGER.debug("ACK received")
                            ack_received = True
                        if _wait_command_answer[-4:] in message:
                            _LOGGER.debug("Answer received")
                            answer_received = True
                        if ack_received and answer_received:
                            return True
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Timeout while waiting for message")
                    except Exception as e:
                        _LOGGER.error(
                            f"Error sending command {command} - {address} - {e}"
                        )
            if not ack_received:
                _LOGGER.error(
                    f"ACK not received on {command} after {attempt + 1} attempts waiting for {_wait_command_ack}"
                )
            if not answer_received:
                _LOGGER.error(
                    f"Answer not received on {command} after {attempt + 1} attempts waiting for {_wait_command_answer}"
                )
            return False

    async def send_command_get_answer(self, command: str, address: str) -> str | None:
        """Send a command and wait for an answer from the Nikobus system."""
        _LOGGER.debug(
            f"Sending command {command} to address {address}, waiting for answer"
        )
        _wait_command_ack, _wait_command_answer = self._prepare_ack_and_answer_signals(
            command, address
        )
        return await self._wait_for_ack_and_answer(
            command, _wait_command_ack, _wait_command_answer
        )

    async def _prepare_values_for_command(self, address: str, group: int) -> bytearray:
        """Fetch the latest values from the hardware and prepare values for a command."""
        # Fetch the latest state from the hardware
        latest_state_hex = await self.get_output_state(address, group)
        if latest_state_hex:
            # Convert the hex string to a bytearray
            latest_state = bytearray.fromhex(latest_state_hex)
            # Update the module state
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = latest_state[:6]
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = latest_state[:6]
            _LOGGER.debug(
                f"Module state for {address}, group {group} updated to: {latest_state[:6]}"
            )
        else:
            _LOGGER.error(
                f"Failed to fetch latest state for module {address}, group {group}"
            )
            # Use the current state if fetching fails
            latest_state = (
                self.nikobus_module_states[address][:6]
                if int(group) == 1
                else self.nikobus_module_states[address][6:12]
            )

        # Append 0xFF to the values as per command format
        values = latest_state[:6] + bytearray([0xFF])
        return values

    def _prepare_ack_and_answer_signals(self, command: str, address: str) -> tuple:
        """Prepare the acknowledgment and answer signals for a command."""
        command_part = command[3:5]
        ack_signal = f"$05{command_part}"
        answer_prefix = "$18" if command_part == "11" else "$1C"
        answer_signal = f"{answer_prefix}{address[2:]}{address[:2]}"
        return ack_signal, answer_signal

    async def _wait_for_ack_and_answer(
        self, command: str, _wait_command_ack: str, _wait_command_answer: str
    ) -> str | None:
        """Wait for an acknowledgment and answer from the Nikobus system."""
        ack_received = False
        answer_received = False
        state = None
        for attempt in range(MAX_ATTEMPTS):
            await self.nikobus_connection.send(command)
            _LOGGER.debug(
                f"Attempt {attempt + 1} of {MAX_ATTEMPTS} waiting for {_wait_command_ack} / {_wait_command_answer}"
            )
            end_time = asyncio.get_event_loop().time() + COMMAND_ACK_WAIT_TIMEOUT
            while asyncio.get_event_loop().time() < end_time:
                try:
                    message = await asyncio.wait_for(
                        self.nikobus_listener.response_queue.get(),
                        timeout=COMMAND_ANSWER_WAIT_TIMEOUT,
                    )
                    _LOGGER.debug(f"Message received: {message}")
                    if _wait_command_ack in message:
                        _LOGGER.debug("ACK received")
                        ack_received = True
                    if _wait_command_answer in message:
                        _LOGGER.debug("Answer received")
                        state = self._parse_state_from_message(
                            message, _wait_command_answer
                        )
                        _LOGGER.debug(f"State from message received: {state}")
                        answer_received = True
                    if ack_received and answer_received:
                        return state
                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        f"Timeout waiting for ACK/Answer on attempt {attempt + 1}"
                    )
        if not ack_received:
            _LOGGER.error(
                f"ACK not received on {command} after {attempt + 1} attempts waiting for {_wait_command_ack}"
            )
        if not answer_received:
            _LOGGER.error(
                f"Answer not received on {command} after {attempt + 1} attempts waiting for {_wait_command_answer}"
            )
        return None

    def _parse_state_from_message(self, message: str, answer_signal: str) -> str:
        """Parse the state from a received message."""
        state_index = message.find(answer_signal) + len(answer_signal) + 2
        return message[state_index : state_index + 12]
