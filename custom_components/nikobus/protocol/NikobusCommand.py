""" Nikobus Command module for Home Assistant """

import logging
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)

class NikobusCommand:
    class Result:
        def __init__(self, result: str = None, exception: Exception = None) -> None:
            if result:
                self._callable = lambda: result
            elif exception:
                self._callable = lambda: exception
            else:
                raise ValueError("Result or Exception must be provided")

        def get(self) -> str:
            result_or_exception = self._callable()
            if isinstance(result_or_exception, str):
                return result_or_exception
            elif isinstance(result_or_exception, Exception):
                raise result_or_exception
            else:
                raise TypeError("Unexpected result type")

    class ResponseHandler:
        def __init__(self, response_length: int, address_start: int, response_code: str,
                     result_consumer: Callable[[Result], None]) -> None:
            self._response_length = response_length
            self._address_start = address_start
            self._response_code = response_code
            self._result_consumer = result_consumer
            self._is_completed = False

        def is_completed(self) -> bool:
            return self._is_completed

        def complete(self, result: Result) -> bool:
            if self._is_completed:
                return False
            self._is_completed = True
            try:
                self._result_consumer(result)
            except Exception as e:
                _LOGGER.warning(f"Processing result {result} failed with {e}")
            return True

        def complete_exceptionally(self, exception: Exception) -> bool:
            return self.complete(NikobusCommand.Result(exception=exception))

        @property
        def response_length(self) -> int:
            return self._response_length

        @property
        def address_start(self) -> int:
            return self._address_start

        @property
        def response_code(self) -> str:
            return self._response_code

    def __init__(self, payload: str, response_length: Optional[int] = None, address_start: Optional[int] = None,
                 response_code: Optional[str] = None, result_consumer: Optional[Callable[[Result], None]] = None):
        self._payload = payload + '\r'
        if response_length is not None and address_start is not None and response_code is not None and result_consumer:
            self._response_handler = self.ResponseHandler(response_length, address_start, response_code, result_consumer)
        else:
            self._response_handler = None

    @property
    def payload(self) -> str:
        return self._payload

    @property
    def response_handler(self) -> Optional[ResponseHandler]:
        return self._response_handler
