"""Command Processing for Nikobus"""

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
__version__ = '0.1'

class NikobusCommandHandler:
    """Handles command processing for Nikobus."""

    def __init__(self, hass, nikobus_connection, nikobus_listener, nikobus_module_states):
        """Initialize the command handler."""
        self._hass = hass
        self._command_task = None
        self._running = False
        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states
        self._command_queue = asyncio.Queue()

    async def start(self):
        """Start the command processing loop."""
        self._running = True
        self._command_task = self._hass.loop.create_task(self.process_commands())

    async def stop(self):
        """Stop the command processing loop."""
        self._running = False
        if self._command_task:
            self._command_task.cancel()  # Request cancellation of the task
            try:
                await self._command_task  # Wait for the task to be cancelled
            except asyncio.CancelledError:
                _LOGGER.info("Command processing task was cancelled")
            self._command_task = None  # Reset the task reference

    async def get_output_state(self, address: str, group: int) -> str:
        """Get the output state of a module."""
        _LOGGER.debug(f'Getting output state - Address: {address}, Group: {group}')
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
        return await self.send_command_get_answer(command, address)

    async def set_output_state(self, address: str, channel: int, value: int) -> None:
        """Set the output state of a module."""
        _LOGGER.debug(f'Setting output state - Address: {address}, Channel: {channel}, Value: {value}')
        group = calculate_group_number(channel)
        command_code = 0x15 if int(group) == 1 else 0x16
        values = self._prepare_values_for_command(address, group)
        command = make_pc_link_command(command_code, address, values)
        await self.queue_command(command)

    async def set_output_states(self, address: str, channel_states: bytearray) -> None:
        """Prepare and queue the output states for a module, split by group if necessary."""
        _LOGGER.debug(f"Preparing to set output states for module {address}: {channel_states.hex()}")
        # Process each group separately
        for group in [1, 2]:
            command_code = 0x15 if group == 1 else 0x16
            values = self._prepare_values_for_command(address, group)
            _LOGGER.debug(f"Queuing command for Group {group} of module {address}: {values.hex()}")
            command = make_pc_link_command(command_code, address, values)
            await self.queue_command(command)

    async def queue_command(self, command: str) -> None:
        """Queue a command for processing."""
        _LOGGER.debug(f'Queueing command: {command}')
        await self._command_queue.put(command)

    async def process_commands(self) -> None:
        """Process commands from the queue."""
        _LOGGER.info("Nikobus Command Processing starting")
        while self._running:
            command = await self._command_queue.get()
            _LOGGER.debug(f'Processing command: {command}')
            await self._execute_command(command)
            await asyncio.sleep(COMMAND_EXECUTION_DELAY)

    async def send_command(self, command: str):
        """Send a command to the Nikobus system."""
        _LOGGER.debug(f'Sending command: {command}')
        try:
            await self.nikobus_connection.send(command)
            _LOGGER.debug('Command sent successfully')
        except Exception as e:
            _LOGGER.error(f'Error sending command: {e}')

    async def send_command_get_answer(self, command: str, address: str) -> str | None:
        """Send a command and wait for an answer from the Nikobus system."""
        _LOGGER.debug(f'Sending command {command} to address {address}, waiting for answer')
        _wait_command_ack, _wait_command_answer = self._prepare_ack_and_answer_signals(command, address)
        return await self._wait_for_ack_and_answer(command, _wait_command_ack, _wait_command_answer)

    async def _execute_command(self, command: str):
        """Execute a command and handle potential errors."""
        try:
            await self.send_command(command)
            _LOGGER.debug(f'Command executed: {command}')
        except Exception as e:
            _LOGGER.error(f'Failed to execute command "{command}": {e}')

    def _prepare_values_for_command(self, address: str, group: int) -> bytearray:
        """Prepare values for a command based on the module state."""
        if int(group) == 1:
            return self.nikobus_module_states[address][:6] + bytearray([0xFF])
        elif int(group) == 2:
            return self.nikobus_module_states[address][6:12] + bytearray([0xFF])

    def _prepare_ack_and_answer_signals(self, command: str, address: str) -> tuple:
        """Prepare the acknowledgment and answer signals for a command."""
        command_part = command[3:5]
        ack_signal = f'$05{command_part}'
        answer_prefix = '$18' if command_part == '11' else '$1C'
        answer_signal = f'{answer_prefix}{address[2:]}{address[:2]}'
        return ack_signal, answer_signal

    async def _wait_for_ack_and_answer(self, command: str, _wait_command_ack: str, _wait_command_answer: str) -> str | None:
        """Wait for an acknowledgment and answer from the Nikobus system."""
        ack_received = False
        answer_received = False
        state = None

        for attempt in range(MAX_ATTEMPTS):
            await self.nikobus_connection.send(command)

            _LOGGER.debug(f'Attempt {attempt + 1} of {MAX_ATTEMPTS} waiting for {_wait_command_ack} / {_wait_command_answer}')
            end_time = asyncio.get_event_loop().time() + COMMAND_ACK_WAIT_TIMEOUT

            while asyncio.get_event_loop().time() < end_time:
                try:
                    message = await asyncio.wait_for(self.nikobus_listener.response_queue.get(), timeout=COMMAND_ANSWER_WAIT_TIMEOUT)
                    _LOGGER.debug(f'Message received: {message}')

                    if _wait_command_ack in message:
                        _LOGGER.debug('ACK received')
                        ack_received = True

                    if _wait_command_answer in message:
                        _LOGGER.debug('Answer received')
                        state = self._parse_state_from_message(message, _wait_command_answer)
                        _LOGGER.debug(f'State from message received: {state}')
                        answer_received = True

                    if ack_received and answer_received:
                        return state

                except asyncio.TimeoutError:
                    _LOGGER.debug(f'Timeout waiting for ACK/Answer on attempt {attempt + 1}')

        if not ack_received:
            _LOGGER.error(f'ACK not received on {command} after {attempt + 1} attempts waiting for {_wait_command_ack}')
        if not answer_received:
            _LOGGER.error(f'Answer not received on {command} after {attempt + 1} attempts waiting for {_wait_command_answer}')

        return None

    def _parse_state_from_message(self, message: str, answer_signal: str) -> str:
        """Parse the state from a received message."""
        state_index = message.find(answer_signal) + len(answer_signal) + 2
        return message[state_index:state_index + 12]
