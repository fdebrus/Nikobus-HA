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

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

BUTTON_STORAGE_KEY = "nikobus.buttons"
BUTTON_STORAGE_VERSION = 1

MODULE_STORAGE_KEY = "nikobus.modules"
MODULE_STORAGE_VERSION = 1

CF_STORAGE_KEY = "nikobus.cfs"
CF_STORAGE_VERSION = 1


class _NikobusStore:
    """Shared HA ``Store`` wrapper keyed by a single root mapping.

    Subclasses set ``_root_key`` (the top-level dict key) and pass their
    storage key + version. ``async_load`` always reshapes a missing or
    malformed payload back to ``{_root_key: {}}`` so the live dict the
    integration and the library share is never ``None`` or wrong-shaped.
    """

    _root_key: str

    def __init__(self, hass: HomeAssistant, key: str, version: int) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, version, key)
        self._key = key
        self._data: dict[str, Any] = {self._root_key: {}}

    async def async_load(self) -> dict[str, Any]:
        """Load persisted data, returning the live mutable dict."""
        loaded = await self._store.async_load()
        if isinstance(loaded, dict) and isinstance(loaded.get(self._root_key), dict):
            self._data = loaded
        else:
            self._data = {self._root_key: {}}
        return self._data

    async def async_save(self) -> None:
        """Persist the current in-memory dict to storage.

        A failed write (disk full, read-only filesystem, …) is logged
        loudly instead of bubbling up: the in-memory dict stays correct
        and the next save retries, whereas an exception here would abort
        the middle of a discovery/reconciliation pass that already
        mutated state.
        """
        try:
            await self._store.async_save(self._data)
        except (OSError, HomeAssistantError):
            _LOGGER.exception(
                "Failed to persist %s to storage — in-memory data is "
                "intact and will be saved again on the next change, but "
                "the on-disk copy is stale until then",
                self._key,
            )

    @property
    def data(self) -> dict[str, Any]:
        """Return the mutable in-memory dict."""
        return self._data

    @property
    def is_empty(self) -> bool:
        """Return True when the root mapping has no entries yet."""
        return not bool(self._data.get(self._root_key))


class NikobusButtonStorage(_NikobusStore):
    """Wrap a HA ``Store`` for button discovery data."""

    _root_key = "nikobus_button"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, BUTTON_STORAGE_KEY, BUTTON_STORAGE_VERSION)


class NikobusModuleStorage(_NikobusStore):
    """Wrap a HA ``Store`` for module configuration (0.4.0 Option-A shape).

    The dict is always reshaped to ``{"nikobus_module": {...}}`` so the
    library's ``setdefault("nikobus_module", {})`` operates on the same
    mapping the integration sees.
    """

    _root_key = "nikobus_module"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, MODULE_STORAGE_KEY, MODULE_STORAGE_VERSION)


class NikobusCFStorage(_NikobusStore):
    """Wrap a HA ``Store`` for classified CF (Central Function) broadcasts.

    Persists the dict the library populates on ``NikobusDiscovery.
    discovered_cf_broadcasts`` after each discovery completes — each
    entry is a ``{bus_address, pattern, outputs}`` record describing a
    CF activation broadcast and its target output channels. Survives
    across HA restarts so scene entities don't disappear when discovery
    isn't re-run.

    Storage shape: ``{"nikobus_cf": {bus_address: {...}}}``.
    """

    _root_key = "nikobus_cf"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, CF_STORAGE_KEY, CF_STORAGE_VERSION)
