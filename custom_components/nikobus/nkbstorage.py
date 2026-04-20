"""HA-native persistence for Nikobus discovery data.

Storage schema (v2, nikobus-connect ≥ 0.3.0):

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

Schema v1 (nikobus-connect 0.2.x) keyed buttons by bus address with a
flat ``linked_button`` / ``linked_modules`` structure. v1 is unreadable
by the new code — the library removed the migration helper — so any v1
file found on disk is dropped with a warning and the user must re-run
discovery. See nikobus-connect#14 for the clean break rationale.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

BUTTON_STORAGE_KEY = "nikobus.buttons"
BUTTON_STORAGE_VERSION = 2


def _looks_like_v1_entry(entry: Any) -> bool:
    """Return True if ``entry`` matches the v1 shape (has linked_button key)."""
    return isinstance(entry, dict) and "linked_button" in entry


class NikobusButtonStorage:
    """Wrap a HA ``Store`` for button discovery data."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, BUTTON_STORAGE_VERSION, BUTTON_STORAGE_KEY
        )
        self._data: dict[str, Any] = {"nikobus_button": {}}

    async def async_load(self) -> dict[str, Any]:
        """Load persisted data, returning a live mutable dict.

        Drops any v1-shaped payload with a warning — users re-run discovery
        to repopulate on the new schema.
        """
        loaded = await self._store.async_load()
        if isinstance(loaded, dict) and isinstance(loaded.get("nikobus_button"), dict):
            buttons = loaded["nikobus_button"]
            if any(_looks_like_v1_entry(entry) for entry in buttons.values()):
                _LOGGER.warning(
                    "Dropping v1 Nikobus button store (legacy schema). "
                    "Run 'Discover modules & buttons' + 'Scan all module links' "
                    "from the Nikobus Bridge device to repopulate."
                )
                self._data = {"nikobus_button": {}}
                await self._store.async_save(self._data)
            else:
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
