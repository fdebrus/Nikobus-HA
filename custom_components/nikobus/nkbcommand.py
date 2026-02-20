"""Nikobus Command Handler - Platinum Edition (Await Fix)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, Awaitable

from .nkbprotocol import make_pc_link_command, calculate_group_number
from .const import (
    COMMAND_EXECUTION_DELAY,
    COMMAND_ACK_WAIT_TIMEOUT,
    MAX_ATTEMPTS,
)
from .exceptions import NikobusSendError, NikobusTimeoutError

_LOGGER = logging.getLogger(__name__)

class NikobusCommandHandler:
    """Handles sequential command processing for the Nikobus PC-Link."""

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
        
        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states

    async def start(self) -> None:
        """Start the consumer loop."""
        self._running = True
        self._command_task = self._coordinator.hass.loop.create_task(self.process_commands())

    async def stop(self) -> None:
        """Stop the command loop and clear tasks."""
        self._running = False
        if self._command_task:
            self._command_task.cancel()
            self._command_task = None

    def _drain_queue(self) -> None:
        """Clear all pending messages from the listener queue."""
        while not self.nikobus_listener.response_queue.empty():
            try:
                self.nikobus_listener.response_queue.get_nowait()
                self.nikobus_listener.response_queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def process_commands(self) -> None:
        """Process commands sequentially from the queue."""
        _LOGGER.info("Nikobus Command Processor active.")
        while self._running:
            try:
                item = await self._command_queue.get()
                command = item["command"]
                address = item.get("address")
                future = item.get("future")
                handler = item.get("completion_handler")

                try:
                    if address:
                        # Status requests or batch updates require waiting for an answer
                        result = await self.send_command_get_answer(command, address)
                        if future and not future.done():
                            future.set_result(result)
                    else:
                        # Fire-and-forget triggers (like button presses)
                        await self.send_command(command)
                    
                    # FIXED: Check if the handler result is awaitable to avoid NoneType errors
                    if handler is not None:
                        res = handler()
                        if inspect.isawaitable(res):
                            await res

                except Exception as err:
                    _LOGGER.error("Command failed: %s | Error: %s", command, err)
                    if future and not future.done():
                        future.set_exception(err)
                finally:
                    self._command_queue.task_done()

                await asyncio.sleep(COMMAND_EXECUTION_DELAY)

            except Exception as loop_err:
                _LOGGER.error("Critical error in command loop: %s", loop_err)

    async def send_command_get_answer(self, command: str, address: str) -> str:
        """Sends hex packet and waits for ACK and Status Answer."""
        swapped_addr = f"{address[2:]}{address[:2]}"
        prefix = "$0EFF" if command.startswith("$1E") else "$1C"
        wait_ack = f"$05{command[3:5]}"
        wait_answer = f"{prefix}{swapped_addr}"

        self._drain_queue()
        
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await self.nikobus_connection.send(command)
                _LOGGER.debug("[%d/%d] Waiting for ACK %s and Answer %s", attempt, MAX_ATTEMPTS, wait_ack, wait_answer)
                
                state = await self._wait_for_response(wait_ack, wait_answer)
                if state:
                    return state
            except (NikobusSendError, NikobusTimeoutError):
                if attempt == MAX_ATTEMPTS:
                    raise
                _LOGGER.warning("Bus busy (Attempt %d). Retrying...", attempt)
        
        raise NikobusTimeoutError(f"No response for {command} after max retries.")

    async def _wait_for_response(self, wait_ack: str, wait_answer: str) -> str | None:
        """Monitor the listener queue for specific hex patterns."""
        start_time = self._coordinator.hass.loop.time()

        while (self._coordinator.hass.loop.time() - start_time) < COMMAND_ACK_WAIT_TIMEOUT:
            try:
                msg = await asyncio.wait_for(self.nikobus_listener.response_queue.get(), timeout=1.0)
                if wait_answer in msg:
                    idx = msg.find(wait_answer) + len(wait_answer) + 2
                    return msg[idx : idx + 12]
            except asyncio.TimeoutError:
                continue
        return None

    async def queue_command(
        self,
        command: str,
        address: str | None = None,
        future: asyncio.Future | None = None,
        completion_handler: Callable | None = None,
    ) -> None:
        """Public method to add a command to the bus queue."""
        await self._command_queue.put({
            "command": command,
            "address": address,
            "future": future,
            "completion_handler": completion_handler,
        })

    async def send_command(self, command: str) -> None:
        """Low-level direct send."""
        try:
            await self.nikobus_connection.send(command)
        except Exception as err:
            raise NikobusSendError(f"Direct send failed: {err}")

    async def set_output_state(self, address: str, channel: int, value: int, completion_handler: Callable | None = None) -> None:
        """Constructs a group command to set a single channel."""
        group = calculate_group_number(channel)
        cmd_code = 0x15 if group == 1 else 0x16

        current_bytes = self._coordinator.get_bytearray_group_state(address, group)
        current_bytes[(channel - 1) % 6] = value
        
        payload = current_bytes[:6] + bytearray([0xFF])
        command = make_pc_link_command(cmd_code, address, payload)
        
        await self.queue_command(command, address, completion_handler=completion_handler)

    async def set_output_states(
        self,
        address: str,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Sets all 6 (or 12) output states for a module at once."""
        _LOGGER.debug("Batch update for module %s", address)
        
        # Group 1 (Channels 1-6)
        channel_states = self.nikobus_module_states[address][:6] + bytearray([0xFF])
        await self.queue_command(
            make_pc_link_command(0x15, address, channel_states),
            address,
            completion_handler=completion_handler,
        )

        # Group 2 (Channels 7-12) if it exists
        if self._coordinator.get_module_channel_count(address) > 6:
            channel_states = self.nikobus_module_states[address][6:12] + bytearray([0xFF])
            await self.queue_command(
                make_pc_link_command(0x16, address, channel_states),
                address,
                completion_handler=completion_handler,
            )

    async def get_output_state(self, address: str, group: int) -> str:
        """Queues a status request for a specific group (1 or 2)."""
        cmd_code = 0x12 if group == 1 else 0x17
        command = make_pc_link_command(cmd_code, address)
        
        future = self._coordinator.hass.loop.create_future()
        await self.queue_command(command, address, future=future)
        return await asyncio.wait_for(future, timeout=5.0)