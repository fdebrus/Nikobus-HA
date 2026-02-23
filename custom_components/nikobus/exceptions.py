# exceptions.py
from homeassistant.exceptions import HomeAssistantError

class NikobusError(HomeAssistantError):
    """Base error for Nikobus."""

class NikobusDataError(NikobusError):
    """Error with Nikobus configuration data."""