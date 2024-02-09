"""Coordinator for Nikobus."""

"""

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Nikobus custom coordinator."""

    def __init__(self, hass: HomeAssistant, api) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
        )
        self.api = api

    async def async_updated_data(self, data) -> None:
        """Update data."""
        super().async_set_updated_data(data)

    def set_updated_data(self, data) -> None:
        """Receive Data."""
        asyncio.run_coroutine_threadsafe(self.async_updated_data(data), self.hass.loop).result()

    def get_value(self, path) -> Any:
        """Return part from document."""
        return self.data.get(path)

    async def turn_on_switch(self, value_path) -> None:
        """Turn on hidro cover."""
        await self.api.turn_on_switch(self.data.id, value_path)

    async def turn_off_switch(self, value_path) -> None:
        """Turn off hidro cover."""
        await self.api.turn_off_switch(self.data.id, value_path)




class NikobusCommand:
    class Result:
        def __init__(self, result: str, exception: Optional[Exception] = None):
            self._callable = lambda: result
            if exception:
                self._callable = lambda: None
                self.exception = exception

        def get(self) -> str:
            if hasattr(self, 'exception'):
                raise self.exception
            return self._callable()

    class ResponseHandler:
        def __init__(self, response_length: int, address_start: int, response_code: str,
                     result_consumer: Callable[[Result], None]):
            self.response_length = response_length
            self.address_start = address_start
            self.response_code = response_code
            self.result_consumer = result_consumer
            self.is_completed = False

        def complete(self, result: Result) -> bool:
            if self.is_completed:
                return False
            self.is_completed = True
            try:
                self.result_consumer(result)
            except Exception as e:
                logger.warning(f"Processing result {result} failed with {e}")
            return True

    def __init__(self, payload: str, response_length: Optional[int] = None, address_start: Optional[int] = None,
                 response_code: Optional[str] = None, result_consumer: Optional[Callable[[Result], None]] = None):
        self.payload = payload + '\r'
        if response_length is not None and address_start is not None and response_code is not None and result_consumer is not None:
            self.response_handler = self.ResponseHandler(response_length, address_start, response_code, result_consumer)
        else:
            self.response_handler = None

    def get_payload(self) -> str:
        return self.payload

    def get_response_handler(self) -> Optional[ResponseHandler]:
        return self.response_handler

class SwitchModuleCommandFactory:
    @staticmethod
    def create_read_command(address: str, group: str, result_consumer: Callable[[Result], None]) -> NikobusCommand:
        SwitchModuleCommandFactory._check_address(address)
        command_payload = append_crc2(f"$10{group.get_status_request()}{address}")
        return NikobusCommand(command_payload, 27, 3, "$1C", result_consumer)

    @staticmethod
    def create_write_command(address: str, group: str, value: str, result_consumer: Callable[[Result], None]) -> NikobusCommand:
        SwitchModuleCommandFactory._check_address(address)
        if len(value) != 12:
            raise ValueError(f"Value must have 12 chars but got '{value}'")
        payload = f"{group.get_status_update()}{address}{value}FF"
        return NikobusCommand(append_crc2(f"$1E{payload}"), 13, 5, "$0E", result_consumer)

    @staticmethod
    def _check_address(address: str) -> None:
        if len(address) != 4:
            raise ValueError(f"Address must have 4 chars but got '{address}'")

class SwitchModuleGroup(Enum):
    FIRST = ("12", "15", 1)
    SECOND = ("17", "16", 7)

    def __init__(self, status_request: str, status_update: str, offset: int):
        self.status_request = status_request
        self.status_update = status_update
        self.offset = offset

    def get_status_request(self) -> str:
        return self.status_request

    def get_status_update(self) -> str:
        return self.status_update

    def get_offset(self) -> int:
        return self.offset

    def get_count(self) -> int:
        return 6

    @staticmethod
    def map_from_channel(channel_uid: ChannelUID) -> 'SwitchModuleGroup':
        if not channel_uid.id_without_group.startswith(CHANNEL_OUTPUT_PREFIX):
            raise ValueError("Unexpected channel {}".format(channel_uid.id))
        channel_number = int(channel_uid.id_without_group[len(CHANNEL_OUTPUT_PREFIX):])
        return SwitchModuleGroup.map_from_channel_number(channel_number)

    @staticmethod
    def map_from_channel_number(channel_number: int) -> 'SwitchModuleGroup':
        max_value = SwitchModuleGroup.SECOND.offset + SwitchModuleGroup.SECOND.get_count()
        if not SwitchModuleGroup.FIRST.offset <= channel_number <= max_value:
            raise ValueError("Channel number should be between [{}, {}], but got {}".format(
                SwitchModuleGroup.FIRST.offset, max_value, channel_number))
        return SwitchModuleGroup.SECOND if channel_number >= SwitchModuleGroup.SECOND.offset else SwitchModuleGroup.FIRST

"""
