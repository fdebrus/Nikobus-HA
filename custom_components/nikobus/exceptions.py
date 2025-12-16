"""Custom exceptions for the Nikobus integration."""

__all__ = [
    "NikobusError",
    "NikobusConnectionError",
    "NikobusDataError",
    "NikobusReadError",
    "NikobusSendError",
    "NikobusTimeoutError",
]


class NikobusError(Exception):
    """Base exception for all Nikobus integration errors."""

    __slots__ = ()


class NikobusConnectionError(NikobusError):
    """Raised when the integration cannot connect to the Nikobus gateway."""

    __slots__ = ()


class NikobusSendError(NikobusError):
    """Raised when sending a command to the gateway fails."""

    __slots__ = ()


class NikobusTimeoutError(NikobusError):
    """Raised when an operation times out waiting for the gateway."""

    __slots__ = ()


class NikobusDataError(NikobusError):
    """Raised when invalid or unexpected data is encountered."""

    __slots__ = ()


class NikobusReadError(NikobusError):
    """Raised when reading data from the gateway fails."""

    __slots__ = ()
