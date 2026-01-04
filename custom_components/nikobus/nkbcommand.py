"""Nikobus Command Handler."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from .nkbprotocol import make_pc_link_command, calculate_group_number
from .const import (
    COMMAND_EXECUTION_DELAY,
    COMMAND_ACK_WAIT_TIMEOUT,
    COMMAND_ANSWER_WAIT_TIMEOUT,
    MAX_ATTEMPTS,
)
from .exceptions import NikobusError, NikobusSendError, NikobusTimeoutError

_LOGGER = logging.getLogger(__name__)


class NikobusCommandHandler:
    """Handles command processing for Nikobus."""

    def __init__(
        self,
        hass: Any,
        coordinator: Any,
        nikobus_connection: Any,
        nikobus_listener: Any,
        nikobus_module_states: dict[str, bytearray],
    ) -> None:
        """Initialize the command handler."""
        self._coordinator = coordinator
        self._running: bool = False
        self._command_task: asyncio.Task | None = None
        self._command_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._command_completion_handlers: dict[str, Callable[[], Awaitable[None]]] = {}

        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states

    async def start(self) -> None:
        """Start the command processing loop."""
        self._running = True
        loop = self._coordinator.hass.loop
        self._command_task = loop.create_task(self.process_commands())

    async def stop(self) -> None:
        """Stop the command processing loop."""
        self._running = False
        if self._command_task:
            self._command_task.cancel()
            try:
                await self._command_task
            except asyncio.CancelledError:
                _LOGGER.info("Command processing task was cancelled.")
            self._command_task = None

    async def clear_command_queue(self) -> None:
        """Clear all pending commands in the queue."""
        while True:
            try:
                self._command_queue.get_nowait()
                self._command_queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def clear_inventory_commands_for_prefix(self, base_command_prefix: str | None) -> None:
        """Clear queued inventory commands matching the provided base prefix."""

        if not base_command_prefix:
            return

        commands_to_keep = []
        prefix = f"$14{base_command_prefix}".upper()

        while True:
            try:
                item = self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            command_str = str(item.get("command", "")).upper()
            matches_prefix = command_str.startswith(prefix)
            self._command_queue.task_done()

            if not matches_prefix:
                commands_to_keep.append(item)

        for item in commands_to_keep:
            await self._command_queue.put(item)

    async def process_commands(self) -> None:
        """Process commands from the queue."""
        _LOGGER.info("Nikobus Command Processing starting.")
        while self._running:
            try:
                command_item = await self._command_queue.get()
                command = command_item["command"]
                address = command_item.get("address")
                future: asyncio.Future | None = command_item.get("future")
                completion_handler: Callable[[], Awaitable[None]] | None = (
                    command_item.get("completion_handler")
                )

                _LOGGER.debug("Dequeued command: %s", command)
                _LOGGER.debug(
                    "Processing command: %s with address: %s", command, address
                )

                try:
                    if not address:
                        await self.send_command(command)
                        if completion_handler and callable(completion_handler):
                            _LOGGER.debug("Calling completion handler for command without address")
                            await completion_handler()
                    else:
                        result = await self.send_command_get_answer(command, address)
                        if future and not future.done():
                            future.set_result(result)
                        if completion_handler and callable(completion_handler):
                            _LOGGER.debug("Calling completion handler")
                            await completion_handler()
                except Exception as err:
                    _LOGGER.error(
                        "Error processing command %s: %s", command, err, exc_info=True
                    )
                    if future and not future.done():
                        future.set_exception(err)
                finally:
                    self._command_queue.task_done()

                await asyncio.sleep(COMMAND_EXECUTION_DELAY)
            except Exception as err:
                _LOGGER.error(
                    "Error in command processing loop: %s", err, exc_info=True
                )

    async def get_output_state(self, address: str, group: int) -> str:
        """Get the output state of a module."""
        _LOGGER.debug("Getting output state - Address: %s, Group: %s", address, group)
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
        future = self._coordinator.hass.loop.create_future()
        await self.queue_command(command, address, future=future)
        return await asyncio.wait_for(future, timeout=COMMAND_ACK_WAIT_TIMEOUT)

    async def send_command_get_answer(self, command: str, address: str) -> str:
        """Send a command and wait for an answer from the Nikobus system."""
        _LOGGER.debug(
            "Sending command %s to address %s, waiting for answer", command, address
        )
        wait_ack, wait_answer = self._prepare_ack_and_answer_signals(command, address)
        state = await self._wait_for_ack_and_answer(command, wait_ack, wait_answer)
        if state is None:
            raise NikobusTimeoutError(
                f"Failed to receive state for command '{command}' after {MAX_ATTEMPTS} attempts."
            )
        return state

    def _prepare_ack_and_answer_signals(
        self, command: str, address: str
    ) -> tuple[str, str]:
        """
        Prepare the acknowledgment and answer signals based on the command prefix.
        For example, command "$1E..." produces ACK="$05XX" and answer signal using a mapped prefix.
        """
        command_prefix = command[:3]
        command_part = command[3:5]
        ack_signal = f"$05{command_part}"

        prefix_mapping = {
            "$1E": "$0EFF",
            "$05": "$1C",
            "$10": "$1C",
        }
        answer_prefix = prefix_mapping.get(command_prefix, "$1C")
        answer_signal = f"{answer_prefix}{address[2:]}{address[:2]}"

        _LOGGER.debug(
            "Prepared signals: ACK=%s, ANSWER=%s, COMMAND=%s, ADDRESS=%s",
            ack_signal,
            answer_signal,
            command,
            address,
        )
        return ack_signal, answer_signal

    async def _wait_for_ack_and_answer(
        self, command: str, wait_ack: str, wait_answer: str
    ) -> str:
        """Wait for an acknowledgment and answer from the Nikobus system with retries."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await self.nikobus_connection.send(command)
                _LOGGER.debug(
                    "Attempt %d/%d waiting for ACK: %s, ANSWER: %s",
                    attempt,
                    MAX_ATTEMPTS,
                    wait_ack,
                    wait_answer,
                )
                state = await self._wait_for_ack_and_answer_state(wait_ack, wait_answer)
                if state is not None:
                    _LOGGER.debug("Received valid state from device.")
                    return state
            except (NikobusSendError, NikobusTimeoutError) as err:
                _LOGGER.warning("Attempt %d error: %s", attempt, err, exc_info=True)
                if attempt == MAX_ATTEMPTS:
                    raise
            except Exception as err:
                _LOGGER.error(
                    "Unhandled exception on attempt %d: %s", attempt, err, exc_info=True
                )
                if attempt == MAX_ATTEMPTS:
                    raise NikobusError(f"Unhandled exception: {err}") from err
        raise NikobusTimeoutError(
            f"Failed to receive ACK and state for command '{command}' after {MAX_ATTEMPTS} attempts."
        )

    async def _wait_for_ack_and_answer_state(
        self, wait_ack: str, wait_answer: str
    ) -> str | None:
        """Wait for both acknowledgment and answer signals, then extract the state."""
        ack_received = False
        answer_received = False
        state: str | None = None
        loop = self._coordinator.hass.loop
        end_time = loop.time() + COMMAND_ACK_WAIT_TIMEOUT

        while loop.time() < end_time:
            try:
                remaining = end_time - loop.time()
                message = await asyncio.wait_for(
                    self.nikobus_listener.response_queue.get(),
                    timeout=min(COMMAND_ANSWER_WAIT_TIMEOUT, remaining),
                )
                _LOGGER.debug("Message received: %s", message)
                if wait_ack in message:
                    _LOGGER.debug("ACK received")
                    ack_received = True
                if wait_answer in message:
                    _LOGGER.debug("Answer received")
                    state = self._parse_state_from_message(message, wait_answer)
                    answer_received = True
                if ack_received and answer_received:
                    return state
            except asyncio.TimeoutError:
                _LOGGER.debug("Timeout while waiting for ACK/Answer")
                break
            except Exception as err:
                _LOGGER.error(
                    "Error while waiting for messages: %s", err, exc_info=True
                )
                raise NikobusError(f"Error while waiting for messages: {err}") from err

        return None

    def _parse_state_from_message(self, message: str, answer_signal: str) -> str:
        """Parse and return the state from a received message."""
        state_index = message.find(answer_signal) + len(answer_signal) + 2
        return message[state_index : state_index + 12]

    async def set_output_state(
        self,
        address: str,
        channel: int,
        value: int,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Set the output state of a module."""
        _LOGGER.debug(
            "Setting output state - Address: %s, Channel: %d, Value: %d",
            address,
            channel,
            value,
        )
        group = calculate_group_number(channel)
        command_code = 0x15 if int(group) == 1 else 0x16

        values = await self._prepare_values_for_command(address, group)
        values[(channel - 1) % 6] = value

        command = make_pc_link_command(command_code, address, values)
        await self.queue_command(
            command, address, completion_handler=completion_handler
        )
        _LOGGER.debug("Command successfully queued.")

    async def _prepare_values_for_command(self, address: str, group: int) -> bytearray:
        """Prepare the bytearray values for a command using the latest coordinator state."""
        return self._coordinator.get_bytearray_group_state(address, group)[
            :6
        ] + bytearray([0xFF])

    async def queue_command(
        self,
        command: str,
        address: str | None = None,
        future: asyncio.Future[str] | None = None,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Queue a command for processing."""
        _LOGGER.debug("Queueing command: %s", command)
        command_item = {
            "command": command,
            "address": address,
            "future": future,
            "completion_handler": completion_handler,
        }
        await self._command_queue.put(command_item)
        _LOGGER.debug("Command queued: %s", command)

    async def send_command(self, command: str) -> None:
        """Send a command to the Nikobus system."""
        _LOGGER.debug("Sending command: %s", command)
        try:
            await self.nikobus_connection.send(command)
        except NikobusError as err:
            _LOGGER.error("Failed to send command %s: %s", command, err, exc_info=True)
            raise

    async def set_output_states(
        self,
        address: str,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Prepare and queue the output states for a module."""
        _LOGGER.debug("Preparing to set output states for module %s", address)
        channel_states = self.nikobus_module_states[address][:6] + bytearray([0xFF])
        await self.queue_command(
            make_pc_link_command(0x15, address, channel_states),
            address,
            completion_handler=completion_handler,
        )

        # If the module has more than 6 channels, send a second group command.
        if self._coordinator.get_module_channel_count(address) > 6:
            channel_states = self.nikobus_module_states[address][6:12] + bytearray(
                [0xFF]
            )
            await self.queue_command(
                make_pc_link_command(0x16, address, channel_states),
                address,
                completion_handler=completion_handler,
            )
