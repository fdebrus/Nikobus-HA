"""Custom exceptions for the Nikobus integration."""

__all__ = [
    "NikobusDataError",
    "NikobusError",
    "NikobusConnectionError",
    "NikobusReadError",
    "NikobusSendError",
    "NikobusTimeoutError",
]


class NikobusError(Exception):
    """Base exception class for Nikobus errors."""

    __slots__ = ()


class NikobusConnectionError(NikobusError):
    """Exception for connection-related errors."""

    __slots__ = ()


class NikobusSendError(NikobusError):
    """Exception for errors when sending commands."""

    __slots__ = ()


class NikobusTimeoutError(NikobusError):
    """Exception for timeout errors."""

    __slots__ = ()


class NikobusDataError(NikobusError):
    """Exception for data-related errors."""

    __slots__ = ()


class NikobusReadError(NikobusError):
    """Exception for errors when reading data."""

    __slots__ = ()
