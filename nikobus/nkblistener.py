import asyncio
import logging
import time

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

# version-access:
def get_version():
    return __version__

class NikobusEventListener:
    def __init__(self, nikobus_connection, button_discovery_callback):
        self.nikobus_connection = nikobus_connection
        self.response_queue = asyncio.Queue()
        self._button_discovery_callback = button_discovery_callback
        self._last_nikobus_command_received_timestamp = 0

    async def listen_for_events(self) -> None:
        """Listen for events from the Nikobus system and handle them accordingly."""
        _LOGGER.info("Nikobus Event Listener started")
        try:
            while True:
                try:
                    data = await asyncio.wait_for(self.nikobus_connection.read(), timeout=10)
                    if not data:
                        _LOGGER.warning("Nikobus connection closed")
                        break
                    message = data.decode('utf-8').strip()
                    _LOGGER.debug(f"Listener - Receiving message: {message}")

                    asyncio.create_task(self.handle_message(message))
                    
                except asyncio.TimeoutError:
                    _LOGGER.debug("Listener - Read operation timed out. Waiting for next data...")
        except asyncio.CancelledError:
            _LOGGER.info("Event listener was cancelled.")
        except Exception as e:
            _LOGGER.error(f"Error in event listener: {e}", exc_info=True)

    async def handle_message(self, message: str) -> None:
        """Handle incoming messages from the Nikobus system."""
        _LOGGER.debug(f"Handler got message: {message}")

        _button_command_prefix = '#N'
        _ignore_answer = '$0E'

        if message.startswith(_button_command_prefix):
            address = message[2:8]
            _LOGGER.debug(f"Handling button press for address: {address}")

            # Skip button press if time between 2 commands < 150ms
            current_time_millis = int(time.time() * 1000)
            if current_time_millis - self._last_nikobus_command_received_timestamp > 150:
                self._last_nikobus_command_received_timestamp = current_time_millis
                await self._button_discovery_callback(address)
                _LOGGER.debug(f"Processed button press for address: {address}")
            else:
                _LOGGER.debug("Skipping command processing due to time constraint.")
        elif not message.startswith(_ignore_answer):
            _LOGGER.debug(f"Adding message to response queue: {message}")
            await self.response_queue.put(message)
