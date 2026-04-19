"""HA-native persistence for Nikobus discovery data.

Replaces the legacy ``config/nikobus_button_config.json`` file with a
versioned store under ``.storage/``. The on-disk shape is:

    {
        "nikobus_button": {
            "<address>": {
                "description": str,
                "address": str,
                "linked_button": [...],
                "linked_modules": [...],
            },
            ...
        }
    }

The nikobus-connect discovery engine mutates the in-memory dict directly
and invokes ``async_save()`` whenever its state changes.
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
        if isinstance(loaded, dict) and "nikobus_button" in loaded:
            self._data = loaded
            if not isinstance(self._data["nikobus_button"], dict):
                self._data["nikobus_button"] = {}
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
