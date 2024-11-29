# exceptions.py

class NikobusError(Exception):
    """Base exception class for Nikobus errors."""
    pass

class NikobusConnectionError(NikobusError):
    """Exception for connection-related errors."""
    pass

class NikobusSendError(NikobusError):
    """Exception for errors when sending commands."""
    pass

class NikobusTimeoutError(NikobusError):
    """Exception for timeout errors."""
    pass

class NikobusDataError(NikobusError):
    """Exception for data-related errors."""
    pass

class NikobusReadError(NikobusError):
    """Exception for errors when reading data."""
    pass
