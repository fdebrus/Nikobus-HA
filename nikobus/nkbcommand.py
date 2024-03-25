import asyncio
import logging

from .nkbprotocol import (
    make_pc_link_command, 
    calculate_group_number
)

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

# version-access:
def get_version():
    return __version__

class NikobusCommandHandler:
    def __init__(self, nikobus_connection, nikobus_listener, nikobus_module_states):
        self.nikobus_connection = nikobus_connection
        self.nikobus_listener = nikobus_listener
        self.nikobus_module_states = nikobus_module_states
        self._command_queue = asyncio.Queue()

    async def get_output_state(self, address: str, group: int) -> str:
        """Retrieve the current state of an output based on its address and group."""
        _LOGGER.debug(f'get_output_state_nikobus() - Address: {address}, Group: {group}')
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
        return await self.send_command_get_answer(command, address)

    async def set_output_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of an output based on its address, channel, and value."""
        _LOGGER.debug(f'set_output_state_nikobus() - Address: {address}, Channel: {channel}, Value: {value}')
        group = calculate_group_number(channel)
        command_code = 0x15 if int(group) == 1 else 0x16
        if int(group) == 1:
            values = self.nikobus_module_states[address][:6] + bytearray([0xFF])
        elif int(group) == 2:
            values = self.nikobus_module_states[address][6:12] + bytearray([0xFF])
        command = make_pc_link_command(command_code, address, values)
        await self.queue_command(command)

    async def queue_command(self, command: str) -> None:
        """Queue a command for execution."""
        _LOGGER.debug(f'Queueing command for execution: {command}')
        await self._command_queue.put(command)

    async def process_commands(self) -> None:
        """Continuously process commands from the command queue."""
        while True:
            command = await self._command_queue.get()
            _LOGGER.debug(f'Executing command from queue: {command}')
            try:
                await self.send_command(command)
                _LOGGER.debug(f'Command executed successfully: {command}')
            except Exception as e:
                _LOGGER.error(f"Failed to execute command '{command}': {e}")
            finally:
                self._command_queue.task_done()
            await asyncio.sleep(0.3)

    async def send_command(self, command):
        _LOGGER.debug('Entering send_command()')
        _LOGGER.debug(f'Command to send: {command}')
    
        try:
            await self.nikobus_connection.send(command)
            _LOGGER.debug('Command sent successfully')
            return True
        except Exception as e:
            _LOGGER.error(f'Error sending command: {e}')
            return False

    async def send_command_get_answer(self, command: str, address: str, max_attempts: int = 3) -> str | None:
        _LOGGER.debug('Entering send_command_get_answer()')
        _LOGGER.debug(f'Command: {command}, Address: {address}')
        # Define the expected acknowledgment and answer signals based on the command and address.
        _wait_command_ack = '$05' + command[3:5]
        _wait_command_answer = '$1C' + address[2:] + address[:2]
        ack_received = False
        answer_received = False
        state = None

        for attempt in range(max_attempts):
            _LOGGER.debug(f'Attempt {attempt + 1} of {max_attempts}')

            _LOGGER.debug(f'Sent command {command} waiting for ACK: {_wait_command_ack} and ANSWER: {_wait_command_answer}')
            await self.nikobus_connection.send(command)

            end_time = asyncio.get_event_loop().time() + 15  # Set a 15-second timeout

            while asyncio.get_event_loop().time() < end_time:
                try:
                    timeout = end_time - asyncio.get_event_loop().time()  # Calculate remaining time for dynamic timeout
                    message = await asyncio.wait_for(self.nikobus_listener.response_queue.get(), timeout=5)
                    _LOGGER.debug(f'Message received: {message}')
                
                    # Check for ACK and answer in the received message.
                    if _wait_command_ack in message and not ack_received:
                        _LOGGER.debug('ACK received.')
                        ack_received = True

                    if _wait_command_answer in message and not answer_received:
                        _LOGGER.debug('Answer received.')
                        # Extract the state information from the message based on the expected format.
                        state = message[message.find(_wait_command_answer) + len(_wait_command_answer) + 2:][:12]
                        answer_received = True

                    if ack_received and answer_received:
                        break  # Break out of the loop if both ACK and answer have been received
                
                except asyncio.TimeoutError:
                    _LOGGER.debug('Timeout waiting for ACK/Answer.')
                    break  # Exit the while loop and potentially retry if the timeout is reached

            if ack_received and answer_received:
                _LOGGER.debug('Both ACK and Answer received successfully.')
                break  # Exit the for loop if both ACK and answer have been received

        # Log the outcome of receiving the expected responses.
        if not ack_received:
            _LOGGER.debug('ACK not received within timeout period after maximum attempts.')
        if not answer_received:
            _LOGGER.debug('Answer not received within timeout period after maximum attempts.')

        return state

