"""Coordinator for Nikobus integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

# Platinum Library Imports
from nikobusconnect import (
    NikobusConnect,
    NikobusCommandHandler,
    NikobusEventListener,
    NikobusAPI
)

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .config import NikobusConfig

_LOGGER = logging.getLogger(__name__)

# Module types supported for polling
MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")


class NikobusDataCoordinator(DataUpdateCoordinator[bool]):
    """Coordinator for managing asynchronous updates and connections to Nikobus."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = config_entry
        self.connection_string = config_entry.data.get(CONF_CONNECTION_STRING)
        self._refresh_interval = config_entry.data.get(CONF_REFRESH_INTERVAL, 120)
        self._has_feedback_module = config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False)
        self._prior_gen3 = config_entry.data.get(CONF_PRIOR_GEN3, False)

        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=self._async_update_data,
            update_interval=self._get_update_interval(),
        )

        # Initialize the library-based connection
        self.nikobus_connect = NikobusConnect(self.connection_string)
        self.nikobus_config = NikobusConfig(hass)
        
        self.api: NikobusAPI | None = None
        self.listener: NikobusEventListener | None = None
        self.command_handler: NikobusCommandHandler | None = None

        self.dict_module_data: dict[str, Any] = {}
        self.dict_button_data: dict[str, Any] = {}
        self.dict_scene_data: dict[str, Any] = {}
        
        # Local state for HA entities
        self.nikobus_module_states: dict[str, bytearray] = {}

    def _get_update_interval(self) -> timedelta | None:
        """Compute the update interval based on configuration."""
        if self._has_feedback_module or self._prior_gen3:
            return None
        return timedelta(seconds=self._refresh_interval)

    async def connect(self) -> None:
        """Establish connection and load local configuration."""
        try:
            # Connect via library
            await self.nikobus_connect.connect()
            
            # Load local config files using the transformed logic in config.py
            self.dict_module_data = await self.nikobus_config.load_json_data(
                "nikobus_module_config.json", "module"
            )
            self.dict_button_data = await self.nikobus_config.load_json_data(
                "nikobus_button_config.json", "button"
            ) or {"nikobus_button": {}}
            self.dict_scene_data = await self.nikobus_config.load_json_data(
                "nikobus_scene_config.json", "scene"
            )

            self._initialize_state_buffers()

            # Initialize the Regex-based Event Listener
            self.listener = NikobusEventListener(
                self.nikobus_connect, 
                event_callback=self._handle_bus_event
            )
            await self.listener.start()

            # Initialize the Sequentially-locked Command Handler
            self.command_handler = NikobusCommandHandler(
                self.nikobus_connect, 
                self.listener.response_queue
            )

            # Initialize the High-level API with local module metadata
            self.api = NikobusAPI(self.command_handler, self.dict_module_data)

            _LOGGER.info("Nikobus Platinum Coordinator initialized without discovery.")

        except Exception as err:
            _LOGGER.exception("Failed to initialize Nikobus: %s", err)
            raise HomeAssistantError(f"Initialization error: {err}") from err

    def _initialize_state_buffers(self) -> None:
        """Allocate bytearrays and sync them with the library cache."""
        for modules in self.dict_module_data.values():
            module_items = modules.items() if isinstance(modules, dict) else (
                (m.get("address"), m) for m in modules if isinstance(m, dict)
            )
            for address, info in module_items:
                if address:
                    channels = info.get("channels", [])
                    count = len(channels)
                    # Create the local state buffer
                    self.nikobus_module_states[address] = bytearray(count)
                    
                    # THE FIX: Sync this empty buffer to the library immediately
                    if self.command_handler:
                        for i in range(1, count + 1):
                            self.command_handler.set_cached_state(address, i, 0)

    async def _handle_bus_event(self, message: str) -> None:
        """Handle raw bus frames from the library listener."""
        # This is where you would link to your actuator logic for physical buttons
        pass

    async def _async_update_data(self) -> None:
        """Refresh module states from the bus via polling."""
        try:
            for module_type in MODULE_TYPES:
                if module_type in self.dict_module_data:
                    await self._refresh_module_type(self.dict_module_data[module_type])
            return None
        except Exception as err:
            _LOGGER.error("Error fetching Nikobus data: %s", err)
            raise UpdateFailed(f"Data refresh failed: {err}") from err

    async def _refresh_module_type(self, modules_dict: dict[str, Any]) -> None:
        """Refresh all modules of a specific type defined in local config."""
        for address in modules_dict:
            # Tell the library API to refresh the module
            await self.api.set_output_states_for_module(address)
            
            # Dispatch signal to update HA entities for this module
            signal = f"{DOMAIN}_update_{address}"
            async_dispatcher_send(self.hass, signal)

    @callback
    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Return raw byte state for a channel index."""
        state = self.nikobus_module_states.get(address)
        if state and 0 < channel <= len(state):
            return state[channel - 1]
        return 0

    @callback
    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Update local state and sync it to the library's internal cache."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
            if self.command_handler:
                self.command_handler.set_cached_state(address, channel, value)

    def get_light_brightness(self, addr: str, ch: int) -> int: 
        return self.get_bytearray_state(addr, ch)

    def get_switch_state(self, addr: str, ch: int) -> bool: 
        return self.get_bytearray_state(addr, ch) == 0xFF

    async def stop(self) -> None:
        """Shut down the listener and disconnect transport."""
        if self.listener:
            await self.listener.stop()
        await self.nikobus_connect.disconnect()

    @callback
    def get_light_brightness(self, addr: str, ch: int) -> int:
        """Return the brightness (0-255) for a dimmer channel."""
        return self.get_bytearray_state(addr, ch)

    @callback
    def get_switch_state(self, addr: str, ch: int) -> bool:
        """Return True if the switch channel is ON (0xFF)."""
        return self.get_bytearray_state(addr, ch) == 0xFF

    @callback
    def get_cover_state(self, addr: str, ch: int) -> int:
        """Return the current operation state of a cover channel."""
        return self.get_bytearray_state(addr, ch)

    def get_cover_operation_time(self, module_id: str, channel: int, default: float = 30.0) -> float:
        """Fetch travel time for a shutter channel from local config."""
        try:
            mod = self.dict_module_data.get("roller_module", {}).get(module_id, {})
            ch_list = mod.get("channels", [])
            ch_data = ch_list[int(channel) - 1]
            ot = ch_data.get("operation_time")
            return float(ot) if ot and float(ot) > 0 else default
        except (IndexError, ValueError, KeyError):
            return default

    def get_known_entity_unique_ids(self) -> set[str]:
        """Return the set of unique_ids for all configured entities."""
        from .router import build_routing, build_unique_id
        known: set[str] = set()

        routing = build_routing(self.dict_module_data)
        for specs in routing.values():
            for spec in specs:
                known.add(build_unique_id(spec.domain, spec.kind, spec.address, spec.channel))

        for addr in self.dict_button_data.get("nikobus_button", {}):
            known.add(f"{DOMAIN}_button_{addr}")
            known.add(f"{DOMAIN}_push_button_{addr}")

        for scene in self.dict_scene_data.get("scene", []):
            if sid := scene.get("id"):
                known.add(f"{DOMAIN}_scene_{sid}")

        return known