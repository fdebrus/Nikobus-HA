"""HA-native persistence for Nikobus discovery data.

Two parallel Stores back the integration:

* ``.storage/nikobus.buttons`` (``NikobusButtonStorage``) — button discovery
  results. Schema unchanged since nikobus-connect 0.3.0.

* ``.storage/nikobus.modules`` (``NikobusModuleStorage``) — module configuration
  + discovery results, introduced with nikobus-connect 0.4.0. Replaces the
  legacy ``<config>/nikobus_module_config.json`` file entirely. Shape::

      {"nikobus_module": {
          "<address>": {
              "module_type": "switch_module" | "dimmer_module" | "roller_module",
              "description": "<user-editable>",
              "model": "<hw model>",
              "channels": [
                  {"description", "entity_type",
                   "led_on", "led_off",
                   "operation_time_up", "operation_time_down"}, ...
              ],
              "discovered_info": {"name", "device_type", "channels_count"},
          },
          ...
      }}

The nikobus-connect discovery engine owns both dicts and mutates them in
place; the integration calls ``async_save()`` through the callbacks it hands
the library.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

BUTTON_STORAGE_KEY = "nikobus.buttons"
BUTTON_STORAGE_VERSION = 1

MODULE_STORAGE_KEY = "nikobus.modules"
MODULE_STORAGE_VERSION = 1


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


class NikobusModuleStorage:
    """Wrap a HA ``Store`` for module configuration (0.4.0 Option-A shape)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, MODULE_STORAGE_VERSION, MODULE_STORAGE_KEY
        )
        self._data: dict[str, Any] = {"nikobus_module": {}}

    async def async_load(self) -> dict[str, Any]:
        """Load persisted data, returning a live mutable dict.

        The dict is always reshaped to ``{"nikobus_module": {...}}`` so the
        library's ``setdefault("nikobus_module", {})`` operates on the same
        mapping the integration sees.
        """
        loaded = await self._store.async_load()
        if isinstance(loaded, dict) and isinstance(loaded.get("nikobus_module"), dict):
            self._data = loaded
        else:
            self._data = {"nikobus_module": {}}
        return self._data

    async def async_save(self) -> None:
        """Persist the current in-memory dict to storage."""
        await self._store.async_save(self._data)

    @property
    def data(self) -> dict[str, Any]:
        """Return the mutable in-memory dict."""
        return self._data

    @property
    def is_empty(self) -> bool:
        """Return True when no modules are registered yet."""
        return not bool(self._data.get("nikobus_module"))
