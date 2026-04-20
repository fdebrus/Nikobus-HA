"""HA-native persistence for Nikobus discovery data.

Storage schema (nikobus-connect ≥ 0.3.0):

    {
        "nikobus_button": {
            "<physical_address>": {
                "type": str,
                "model": str,
                "channels": int,
                "description": str,
                "operation_points": {
                    "1A": {
                        "bus_address": str,
                        "description": str,
                        "linked_modules": [
                            {"module_address": str, "outputs": [...]},
                            ...
                        ],
                    },
                    "1B": {...},
                    ...
                },
            },
            ...
        }
    }

The nikobus-connect discovery engine owns the dict and mutates it in place;
the integration calls ``async_save()`` through the callback it hands the
library.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

BUTTON_STORAGE_KEY = "nikobus.buttons"
BUTTON_STORAGE_VERSION = 1


class NikobusButtonStorage:
    """Wrap a HA ``Store`` for button discovery data."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, BUTTON_STORAGE_VERSION, BUTTON_STORAGE_KEY
        )
        self._data: dict[str, Any] = {"nikobus_button": {}}

    async def async_load(self) -> dict[str, Any]:
        """Load persisted data, returning a live mutable dict."""
        loaded = await self._store.async_load()
        if isinstance(loaded, dict) and isinstance(loaded.get("nikobus_button"), dict):
            self._data = loaded
        else:
            self._data = {"nikobus_button": {}}
        return self._data

    async def async_save(self) -> None:
        """Persist the current in-memory dict to storage."""
        await self._store.async_save(self._data)

    @property
    def data(self) -> dict[str, Any]:
        """Return the mutable in-memory dict."""
        return self._data
