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

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .discovery import NikobusDiscovery
from .discovery.base import InventoryQueryType
from .exceptions import NikobusConnectionError, NikobusDataError
from .nkbactuator import NikobusActuator
from .nkbAPI import NikobusAPI
from .nkbcommand import NikobusCommandHandler
from .nkbconfig import NikobusConfig
from .nkbconnect import NikobusConnect
from .nkblistener import NikobusEventListener

_LOGGER = logging.getLogger(__name__)

# Module types supported for polling
MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")


class NikobusDataCoordinator(DataUpdateCoordinator[bool]):
    """Coordinator for managing asynchronous updates and connections to Nikobus."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator with Home Assistant and configuration entry."""
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

        self.nikobus_connection = NikobusConnect(self.connection_string)
        self.nikobus_config = NikobusConfig(hass)
        self.api: NikobusAPI | None = None

        self.dict_module_data: dict[str, Any] = {}
        self.dict_button_data: dict[str, Any] = {}
        self.dict_scene_data: dict[str, Any] = {}
        self.nikobus_module_states: dict[str, bytearray] = {}

        self.nikobus_actuator: NikobusActuator | None = None
        self.nikobus_listener: NikobusEventListener | None = None
        self.nikobus_command: NikobusCommandHandler | None = None
        self.nikobus_discovery: NikobusDiscovery | None = None

        self._discovery_running = False
        self._discovery_module = None
        self.discovery_module_address: str | None = None
        self.inventory_query_type: InventoryQueryType | None = None
        self._reload_task = None

    def _get_update_interval(self) -> timedelta | None:
        """Compute the update interval based on configuration."""
        if self._has_feedback_module or self._prior_gen3:
            return None
        return timedelta(seconds=self._refresh_interval)

    async def connect(self) -> None:
        """Establish connection and initialize Nikobus components."""
        try:
            await self.nikobus_connection.connect()
        except NikobusConnectionError as err:
            _LOGGER.error("Failed to connect to Nikobus: %s", err)
            raise

        try:
            self.dict_module_data = await self.nikobus_config.load_json_data(
                "nikobus_module_config.json", "module"
            )
            discovered_modules = await self.nikobus_config.load_optional_json_data(
                "nikobus_module_discovered.json", "module"
            )
            if discovered_modules:
                self._merge_discovered_modules(discovered_modules)

            self.dict_button_data = await self.nikobus_config.load_json_data(
                "nikobus_button_config.json", "button"
            ) or {"nikobus_button": {}}
            self.dict_scene_data = await self.nikobus_config.load_json_data(
                "nikobus_scene_config.json", "scene"
            )

            self._initialize_state_buffers()

            self.nikobus_actuator = NikobusActuator(
                self.hass, self, self.dict_button_data, self.dict_module_data
            )
            self.nikobus_discovery = NikobusDiscovery(self.hass, self)
            self.nikobus_discovery.on_discovery_finished = self._handle_discovery_finished
            self.nikobus_listener = NikobusEventListener(
                self.hass, self.config_entry, self, self.nikobus_actuator,
                self.nikobus_connection, self.nikobus_discovery, self.process_feedback_data
            )
            self.nikobus_command = NikobusCommandHandler(
                self.hass, self, self.nikobus_connection, self.nikobus_listener,
                self.nikobus_module_states
            )

            self.api = NikobusAPI(self.hass, self)
            await self.nikobus_command.start()
            await self.nikobus_listener.start()

            await self.async_refresh()
        except Exception as err:
            _LOGGER.exception("Failed to initialize Nikobus components: %s", err)
            raise HomeAssistantError(f"Initialization error: {err}") from err

    def _initialize_state_buffers(self) -> None:
        """Allocate bytearrays based on the channel count of discovered modules."""
        for modules in self.dict_module_data.values():
            module_items = modules.items() if isinstance(modules, dict) else (
                (m.get("address"), m) for m in modules if isinstance(m, dict)
            )
            for address, info in module_items:
                if address:
                    channels = info.get("channels", [])
                    self.nikobus_module_states[address] = bytearray(len(channels))

    async def _async_update_data(self) -> bool:
        """Refresh latest data from the Nikobus system via polling."""
        if self._discovery_running:
            return False

        try:
            for module_type in MODULE_TYPES:
                if module_type in self.dict_module_data:
                    await self._refresh_module_type(self.dict_module_data[module_type])
            return True
        except NikobusDataError as err:
            _LOGGER.error("Error fetching Nikobus data: %s", err)
            raise UpdateFailed(f"Data refresh failed: {err}") from err

    async def _refresh_module_type(self, modules_dict: dict[str, Any]) -> None:
        """Poll the bus for each module address in a collection."""
        for address, module_data in modules_dict.items():
            channels = module_data.get("channels", [])
            chan_count = len(channels)
            groups = (1,) if chan_count <= 6 else (1, 2)
            
            group_states = []
            for group in groups:
                state = await self.nikobus_command.get_output_state(address, group) or ""
                group_states.append(state)
            
            state_hex = "".join(group_states).ljust(chan_count * 2, "0")
            try:
                self.nikobus_module_states[address] = bytearray.fromhex(state_hex)
            except ValueError:
                self.nikobus_module_states[address] = bytearray(chan_count)

    async def process_feedback_data(self, group: int, data: str) -> None:
        """Handle incoming feedback module data strings."""
        try:
            addr_raw = data[3:7]
            address = addr_raw[2:] + addr_raw[:2]
            state_raw = data[9:21]

            if address not in self.nikobus_module_states:
                self.nikobus_module_states[address] = bytearray(12)

            if group == 1:
                self.nikobus_module_states[address][:6] = bytearray.fromhex(state_raw)
            elif group == 2:
                self.nikobus_module_states[address][6:] = bytearray.fromhex(state_raw)

            # Signal only entities on this specific module address
            await self.async_event_handler(
                "nikobus_refreshed",
                {"impacted_module_address": address, "impacted_module_group": group},
            )
        except Exception as err:
            _LOGGER.error("Feedback processing error: %s", err)

    @callback
    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Return raw byte state for a channel index."""
        state = self.nikobus_module_states.get(address)
        if state and 0 < channel <= len(state):
            return state[channel - 1]
        return 0

    @callback
    def get_bytearray_group_state(self, address: str, group: int) -> bytearray:
        """Retrieve current 6-byte group state for commands."""
        group = int(group)
        state = self.nikobus_module_states.get(address)
        if not state:
            return bytearray(6)
        if group == 1:
            return state[0:6].ljust(6, b'\x00')
        if group == 2:
            return state[6:12].ljust(6, b'\x00')
        return bytearray(6)

    @callback
    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Manually update a specific channel in the state buffer."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Safely update a module group from a hex string."""
        if address not in self.nikobus_module_states:
            return
        state = self.nikobus_module_states[address]
        new_values = bytearray.fromhex(value)
        if int(group) == 1:
            limit = min(6, len(state))
            state[0:limit] = new_values[:limit]
        elif int(group) == 2 and len(state) > 6:
            limit = min(6, len(state) - 6)
            state[6 : 6 + limit] = new_values[:limit]

    async def async_event_handler(self, event: str, data: dict[str, Any]) -> None:
        """Dispatch events and trigger targeted entity updates."""
        if event == "ha_button_pressed":
            await self.nikobus_command.queue_command(f"#N{data.get('address')}\r#E1")
        
        # Targeted update for the specific module address using Dispatcher
        if address := data.get("impacted_module_address"):
            signal = f"{DOMAIN}_update_{address}"
            _LOGGER.debug("Targeted update signal sent for module: %s", address)
            async_dispatcher_send(self.hass, signal)
        else:
            # Broadcast to everyone as a fallback
            _LOGGER.debug("Global broadcast refresh triggered")
            self.async_update_listeners()

    def get_module_type(self, module_id: str) -> str | None:
        """Return the hardware type of the specified module."""
        for m_type, modules in self.dict_module_data.items():
            if module_id in modules:
                return m_type
        return None

    def get_module_channel_count(self, module_id: str) -> int:
        """Return the count of channels configured for a module."""
        for modules in self.dict_module_data.values():
            if data := modules.get(module_id):
                return len(data.get("channels", []))
        return 0

    def get_cover_operation_time(self, module_id: str, channel: int, default: float = 30.0) -> float:
        """Fetch travel time for a shutter channel."""
        try:
            mod = self.dict_module_data.get("roller_module", {}).get(module_id, {})
            ch = mod.get("channels", [])[int(channel) - 1]
            ot = ch.get("operation_time")
            return float(ot) if ot and float(ot) > 0 else default
        except (IndexError, ValueError):
            return default

    def get_light_brightness(self, addr: str, ch: int) -> int: return self.get_bytearray_state(addr, ch)
    def get_switch_state(self, addr: str, ch: int) -> bool: return self.get_bytearray_state(addr, ch) == 0xFF
    def get_cover_state(self, addr: str, ch: int) -> int: return self.get_bytearray_state(addr, ch)

    async def stop(self) -> None:
        """Shut down background tasks and disconnect."""
        if self.nikobus_listener:
            await self.nikobus_listener.stop()
        if self.nikobus_command:
            await self.nikobus_command.stop()
        await self.nikobus_connection.disconnect()

    def get_known_entity_unique_ids(self) -> set[str]:
        """Return the set of valid unique_ids for all Nikobus entities."""
        from .router import build_routing, build_unique_id
        known: set[str] = set()

        # 1. Module-based entities (Cover, Light, Switch)
        routing = build_routing(self.dict_module_data)
        for specs in routing.values():
            for spec in specs:
                known.add(build_unique_id(spec.domain, spec.kind, spec.address, spec.channel))

        # 2. Buttons (Sensors and Push buttons)
        for addr in self.dict_button_data.get("nikobus_button", {}):
            # Matches binary_sensor.py: f"{DOMAIN}_button_{address}"
            known.add(f"{DOMAIN}_button_{addr}")
            # Matches button.py: f"{DOMAIN}_push_button_{address}"
            known.add(f"{DOMAIN}_push_button_{addr}")

        # 3. Scenes
        for scene in self.dict_scene_data.get("scene", []):
            if sid := scene.get("id"):
                known.add(f"{DOMAIN}_scene_{sid}")

        return known

    def _merge_discovered_modules(self, discovered: dict[str, Any]) -> None:
        """Integrate newly discovered hardware into the registry."""
        for m_type, modules in discovered.items():
            target = self.dict_module_data.setdefault(m_type, {})
            for addr, info in modules.items():
                if addr not in target:
                    target[addr] = info

    async def _handle_discovery_finished(self) -> None:
        """Reload config entry once discovery is complete."""
        self._discovery_running = False
        if self._reload_task and not self._reload_task.done():
            return
        async def _reload():
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        self._reload_task = self.hass.async_create_task(_reload())