"""Custom exceptions for the Nikobus integration."""


class NikobusError(Exception):
    """Base exception class for Nikobus errors."""


class NikobusConnectionError(NikobusError):
    """Exception for connection-related errors."""


class NikobusSendError(NikobusError):
    """Exception for errors when sending commands."""


class NikobusTimeoutError(NikobusError):
    """Exception for timeout errors."""


class NikobusDataError(NikobusError):
    """Exception for data-related errors."""


class NikobusReadError(NikobusError):
    """Exception for errors when reading data."""
