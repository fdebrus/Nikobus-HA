"""Command Processing for Nikobus."""

import asyncio
import logging

from .nkbprotocol import make_pc_link_command, calculate_group_number

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

COMMAND_EXECUTION_DELAY = 0.3  # Delay between command executions in seconds
COMMAND_ACK_WAIT_TIMEOUT = 15  # Timeout for waiting for command ACK in seconds
COMMAND_ANSWER_WAIT_TIMEOUT = 5  # Timeout for waiting for command answer in each loop
MAX_ATTEMPTS = 3  # Maximum attempts for sending commands and waiting for an answer

class NikobusCommandHandler:
    def __init__(self, hass, nikobus_connection, nikobus_listener, nikobus_module_states):
        self._hass = hass
        self._command_task = None
        self._running = False
        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states
        self._command_queue = asyncio.Queue()

    async def start(self):
        self._running = True
        self._command_task = self._hass.loop.create_task(self.process_commands())

    async def stop(self):
        self._running = False
        if self._command_task:
            self._command_task.cancel()  # Request cancellation of the task
            try:
                await self._command_task  # Wait for the task to be cancelled
            except asyncio.CancelledError:
                _LOGGER.info("Command processing task was cancelled.")
            self._command_task = None  # Reset the task reference

    async def get_output_state(self, address: str, group: int) -> str:
        _LOGGER.debug(f'Getting output state - Address: {address}, Group: {group}')
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
        return await self.send_command_get_answer(command, address)

    async def set_output_state(self, address: str, channel: int, value: int) -> None:
        _LOGGER.debug(f'Setting output state - Address: {address}, Channel: {channel}, Value: {value}')
        group = calculate_group_number(channel)
        command_code = 0x15 if int(group) == 1 else 0x16
        values = self._prepare_values_for_command(address, group)
        command = make_pc_link_command(command_code, address, values)
        await self.queue_command(command)

    async def queue_command(self, command: str) -> None:
        _LOGGER.debug(f'Queueing command: {command}')
        await self._command_queue.put(command)

    async def process_commands(self) -> None:
        _LOGGER.info("Nikobus Command Processing started")
        while self._running:
            command = await self._command_queue.get()
            _LOGGER.debug(f'Processing command: {command}')
            await self._execute_command(command)
            await asyncio.sleep(COMMAND_EXECUTION_DELAY)

    async def send_command(self, command):
        _LOGGER.debug(f'Sending command: {command}')
        try:
            await self.nikobus_connection.send(command)
            _LOGGER.debug('Command sent successfully')
        except Exception as e:
            _LOGGER.error(f'Error sending command: {e}')

    async def send_command_get_answer(self, command: str, address: str) -> str | None:
        _LOGGER.debug(f'Sending command {command} address {address} waiting for answer')
        _wait_command_ack, _wait_command_answer = self._prepare_ack_and_answer_signals(command, address)
        return await self._wait_for_ack_and_answer(command, _wait_command_ack, _wait_command_answer)

    async def _execute_command(self, command: str):
        try:
            await self.send_command(command)
            _LOGGER.debug(f'Command executed: {command}')
        except Exception as e:
            _LOGGER.error(f'Failed to execute command "{command}": {e}')

    def _prepare_values_for_command(self, address: str, group: int) -> bytearray:
        if int(group) == 1:
            return self.nikobus_module_states[address][:6] + bytearray([0xFF])
        elif int(group) == 2:
            return self.nikobus_module_states[address][6:12] + bytearray([0xFF])

    def _prepare_ack_and_answer_signals(self, command: str, address: str) -> tuple:
        ack_signal = f'$05{command[3:5]}'
        answer_signal = f'$1C{address[2:]}{address[:2]}'
        return ack_signal, answer_signal

    async def _wait_for_ack_and_answer(self, command:str, _wait_command_ack: str, _wait_command_answer: str) -> str | None:
        ack_received = False
        answer_received = False
        state = None

        for attempt in range(MAX_ATTEMPTS):
            _LOGGER.debug(f'Attempt {attempt + 1} of {MAX_ATTEMPTS} waiting for {_wait_command_ack} {_wait_command_answer}')
            await self.nikobus_connection.send(command)

            end_time = asyncio.get_event_loop().time() + COMMAND_ACK_WAIT_TIMEOUT

            while asyncio.get_event_loop().time() < end_time:
                try:
                    timeout = end_time - asyncio.get_event_loop().time()
                    message = await asyncio.wait_for(self.nikobus_listener.response_queue.get(), timeout=COMMAND_ANSWER_WAIT_TIMEOUT)
                    _LOGGER.debug(f'Message received: {message}')

                    if _wait_command_ack in message and not ack_received:
                        _LOGGER.debug('ACK received.')
                        ack_received = True

                    if _wait_command_answer in message and not answer_received:
                        _LOGGER.debug('Answer received.')
                        state = self._parse_state_from_message(message, _wait_command_answer)
                        answer_received = True

                    if ack_received and answer_received:
                        break
                
                except asyncio.TimeoutError:
                    _LOGGER.debug('Timeout waiting for ACK/Answer.')
                    break

            if ack_received and answer_received:
                _LOGGER.debug('Both ACK and Answer received successfully.')
                return state

        if not ack_received:
            _LOGGER.debug('ACK not received within timeout period.')
        if not answer_received:
            _LOGGER.debug('Answer not received within timeout period.')

        return state

    def _parse_state_from_message(self, message: str, answer_signal: str) -> str:
        state_index = message.find(answer_signal) + len(answer_signal) + 2
        return message[state_index:state_index+12]