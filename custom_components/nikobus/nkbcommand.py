"""Nikobus Command Handler."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from homeassistant.core import HomeAssistant

from .nkbprotocol import make_pc_link_command, calculate_group_number
from .const import (
    COMMAND_EXECUTION_DELAY,
    COMMAND_ACK_WAIT_TIMEOUT,
    COMMAND_ANSWER_WAIT_TIMEOUT,
    COMMAND_POST_ACK_ANSWER_TIMEOUT,
    MAX_ATTEMPTS,
)
from .exceptions import NikobusError, NikobusSendError, NikobusTimeoutError

if TYPE_CHECKING:
    from .nkbconnect import NikobusConnect
    from .nkblistener import NikobusEventListener
    from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


class NikobusCommandHandler:
    """Handles command processing for Nikobus."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        nikobus_connection: NikobusConnect,
        nikobus_listener: NikobusEventListener,
        nikobus_module_states: dict[str, bytearray],
    ) -> None:
        """Initialize the command handler."""
        self._coordinator = coordinator
        self._running: bool = False
        self._command_task: asyncio.Task | None = None
        self._command_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._command_completion_handlers: dict[str, Callable[[], Awaitable[None]]] = {}
        # Keyed by "ADDRESS_group" — resolved directly by the feedback callback so
        # get_output_state returns as soon as the PCLink echoes the response rather
        # than waiting for the queue-polling path to complete (restores 0.7.x behaviour).
        self._pending_get_futures: dict[str, asyncio.Future[str]] = {}
        # Tracks "ADDRESS_group" keys for GET commands currently sitting in the
        # command queue (not yet being processed).  Used by queue_command to suppress
        # duplicate GETs for the same module/group: the new caller's future is already
        # registered in _pending_get_futures and will be resolved by the existing
        # command's feedback fast-path, so no second bus round-trip is needed.
        self._queued_get_keys: set[str] = set()

        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states

    async def start(self) -> None:
        """Start the command processing loop."""
        self._running = True
        self._command_task = self._coordinator.hass.async_create_background_task(
            self.process_commands(), name="nikobus_command_loop"
        )

    async def stop(self) -> None:
        """Stop the command processing loop."""
        self._running = False
        # Cancel any futures that are still waiting for a bus response so that
        # callers (e.g. get_output_state) don't hang for COMMAND_ACK_WAIT_TIMEOUT
        # (15 s) during HA shutdown.
        for future in list(self._pending_get_futures.values()):
            if not future.done():
                future.cancel()
        self._pending_get_futures.clear()
        self._queued_get_keys.clear()
        if self._command_task:
            self._command_task.cancel()
            try:
                await self._command_task
            except asyncio.CancelledError:
                _LOGGER.info("Command processing task was cancelled.")
            self._command_task = None

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

                _LOGGER.debug("Processing command: %s with address: %s", command, address)

                # Release dedup lock as soon as the command is dequeued so that
                # any new GET for the same module/group arriving during the bus
                # round-trip is queued normally rather than suppressed.
                gid = command[3:5] if len(command) >= 5 else ""
                if gid in ("12", "17") and address:
                    self._queued_get_keys.discard(
                        f"{address.upper()}_{'1' if gid == '12' else '2'}"
                    )

                try:
                    if not address:
                        await self.send_command(command)
                        if completion_handler and callable(completion_handler):
                            _LOGGER.debug("Calling completion handler for command without address")
                            res = completion_handler()
                            if inspect.isawaitable(res):
                                await res
                    else:
                        result = await self.send_command_get_answer(command, address)
                        if future and not future.done():
                            future.set_result(result)
                        if completion_handler and callable(completion_handler):
                            _LOGGER.debug("Calling completion handler")
                            res = completion_handler()
                            if inspect.isawaitable(res):
                                await res
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
        key = f"{address.upper()}_{group}"
        self._pending_get_futures[key] = future
        try:
            await self.queue_command(command, address, future=future)
            return await asyncio.wait_for(future, timeout=COMMAND_ACK_WAIT_TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            if not future.done():
                future.cancel()
            raise
        finally:
            self._pending_get_futures.pop(key, None)

    def resolve_pending_get(self, address: str, group: int, state: str) -> None:
        """Resolve a pending get_output_state future directly from the feedback callback.

        Called by the coordinator's process_feedback_data when the PCLink module
        echoes a $1C… response.  This restores the 0.7.x fast-path: get_output_state
        returns as soon as the frame arrives rather than waiting for the queue-polling
        loop to pick it up, which could stall for up to COMMAND_POST_ACK_ANSWER_TIMEOUT
        under bus contention.
        """
        key = f"{address.upper()}_{group}"
        future = self._pending_get_futures.get(key)
        if future and not future.done():
            _LOGGER.debug(
                "Feedback fast-path: resolving pending GET for %s group %s", address, group
            )
            future.set_result(state)

    async def send_command_get_answer(self, command: str, address: str) -> str:
        """Send a command and wait for an answer from the Nikobus system."""
        _LOGGER.debug(
            "Sending command %s to address %s, waiting for answer", command, address
        )
        wait_ack, wait_answer = self._prepare_ack_and_answer_signals(command, address)

        # For GET commands ($1012 = group 1, $1017 = group 2), tell the listener
        # which group is about to be queried so that the feedback callback can
        # attribute the matching $1C response to the correct group.  This is
        # required because many PC-Link firmware variants do not echo HA's own
        # bus commands back on the serial port, leaving the echo-based
        # _last_query_group dict empty and causing all responses to default to
        # group 1 — which corrupts the state buffer of 12-channel modules.
        gid = command[3:5] if len(command) >= 5 else ""
        if gid in ("12", "17"):
            self.nikobus_listener.set_pending_query_group(
                address.upper(), 1 if gid == "12" else 2
            )
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
            # Flush stale messages before each attempt — a failed previous attempt
            # may have left non-matching frames (foreign bus traffic, #N button
            # frames on old firmware) in the queue that would delay the next try.
            while not self.nikobus_listener.response_queue.empty():
                try:
                    self.nikobus_listener.response_queue.get_nowait()
                    self.nikobus_listener.response_queue.task_done()
                except asyncio.QueueEmpty:
                    break
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
                # Once the ACK is in hand the data response must follow within
                # COMMAND_POST_ACK_ANSWER_TIMEOUT (typically 1.5 s).  Before the
                # ACK we allow the full COMMAND_ANSWER_WAIT_TIMEOUT so that a
                # slow module still gets a fair chance on the first attempt.
                per_msg_timeout = (
                    COMMAND_POST_ACK_ANSWER_TIMEOUT if ack_received
                    else COMMAND_ANSWER_WAIT_TIMEOUT
                )
                message = await asyncio.wait_for(
                    self.nikobus_listener.response_queue.get(),
                    timeout=min(per_msg_timeout, remaining),
                )
                self.nikobus_listener.response_queue.task_done()
                _LOGGER.debug("Message received: %s", message)
                if wait_ack in message:
                    _LOGGER.debug("ACK received")
                    ack_received = True
                if wait_answer in message:
                    # $0EFF responses are set-command acks — no 12-byte state payload
                    # $1C responses are get-command answers — contain 12-byte state + CRC
                    if wait_answer.startswith("$0EFF"):
                        _LOGGER.debug("Answer received (set-command ack)")
                        state = ""
                        answer_received = True
                    elif len(message) >= len(wait_answer) + 2 + 12:
                        _LOGGER.debug("Answer received")
                        state = self._parse_state_from_message(message, wait_answer)
                        answer_received = True
                    else:
                        _LOGGER.debug(
                            "Ignoring short get-response (len=%d, need>=%d): %s",
                            len(message), len(wait_answer) + 2 + 12, message,
                        )
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
        idx = message.find(answer_signal)
        if idx == -1:
            _LOGGER.warning("Answer signal %s not found in message: %s", answer_signal, message)
            return ""
        state_index = idx + len(answer_signal) + 2
        state = message[state_index : state_index + 12]
        if len(state) < 12:
            _LOGGER.warning(
                "State data truncated (%d/12 chars) in message: %s", len(state), message
            )
            return ""
        return state

    async def set_output_state(
        self,
        address: str,
        channel: int,
        value: int,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Sets a single channel state and queues the command instantly."""
        _LOGGER.debug(
            "Setting output state - Address: %s, Channel: %d, Value: %d",
            address, channel, value
        )
        group = calculate_group_number(channel)

        # 1. Persist the new value into the coordinator's state buffer immediately.
        # get_bytearray_group_state() returns a copy, so we must write back via
        # set_bytearray_state() to ensure rapid successive commands each see the
        # cumulative state of all previous ones.
        self._coordinator.set_bytearray_state(address, channel, value)
        current_bytes = self._coordinator.get_bytearray_group_state(address, group)

        # 2. Build the payload using this updated state
        cmd_code = 0x15 if group == 1 else 0x16
        payload = current_bytes[:6] + bytearray([0xFF])
        
        # 3. Bake the final string command immediately
        command = make_pc_link_command(cmd_code, address, payload)
        
        # 4. Put it in the queue. The existing `process_commands` loop will handle it
        # and enforce the necessary pause using COMMAND_EXECUTION_DELAY.
        await self.queue_command(
            command, address, completion_handler=completion_handler
        )
        _LOGGER.debug("Command successfully queued for module %s, channel %d.", address, channel)

    async def queue_command(
        self,
        command: str,
        address: str | None = None,
        future: asyncio.Future[str] | None = None,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Queue a command for processing."""
        _LOGGER.debug("Queueing command: %s", command)

        # Suppress duplicate GET commands for the same module/group while an
        # identical GET is already sitting in the queue (not yet dispatched).
        # The new caller's future is already stored in _pending_get_futures by
        # get_output_state(); the existing command's feedback fast-path will
        # resolve it when the bus response arrives, so no extra round-trip is
        # needed.  The dedup key is cleared the moment the command is dequeued
        # (before the bus round-trip) so GETs arriving during the round-trip
        # are queued normally.
        gid = command[3:5] if len(command) >= 5 else ""
        if gid in ("12", "17") and address:
            dedup_key = f"{address.upper()}_{'1' if gid == '12' else '2'}"
            if dedup_key in self._queued_get_keys:
                _LOGGER.debug(
                    "Suppressing duplicate GET for %s (already queued)", dedup_key
                )
                return
            self._queued_get_keys.add(dedup_key)

        command_item = {
            "command": command,
            "address": address,
            "future": future,
            "completion_handler": completion_handler,
        }
        try:
            self._command_queue.put_nowait(command_item)
        except asyncio.QueueFull:
            _LOGGER.warning("Command queue full — dropping command: %s", command)
            if future and not future.done():
                future.set_exception(NikobusError("Command queue full"))
            raise NikobusError("Command queue full")
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
        state = self.nikobus_module_states.get(str(address).upper())
        if state is None:
            _LOGGER.warning("Cannot set output states — module %s not in state buffer", address)
            return
        has_second_group = self._coordinator.get_module_channel_count(address) > 6
        channel_states = state[:6] + bytearray([0xFF])
        await self.queue_command(
            make_pc_link_command(0x15, address, channel_states),
            address,
            # Only fire the completion handler on the last queued command so it
            # fires exactly once per set_output_states call.
            completion_handler=None if has_second_group else completion_handler,
        )

        # If the module has more than 6 channels, send a second group command.
        if has_second_group:
            channel_states = state[6:12] + bytearray([0xFF])
            await self.queue_command(
                make_pc_link_command(0x16, address, channel_states),
                address,
                completion_handler=completion_handler,
            )