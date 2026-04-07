"""Coordinator for Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from nikobus_connect import NikobusAPI, NikobusCommandHandler, NikobusConnect, NikobusEventListener
from nikobus_connect.exceptions import NikobusConnectionError, NikobusDataError

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY_ANSWER,
    DOMAIN,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
)
from nikobus_connect.discovery import NikobusDiscovery, InventoryQueryType
from .nkbactuator import NikobusActuator
from .nkbconfig import NikobusConfig

_LOGGER = logging.getLogger(__name__)

# Module types supported for polling
MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")


class NikobusDataCoordinator(DataUpdateCoordinator[None]):
    """Coordinator for managing asynchronous updates and connections to Nikobus."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = config_entry
        self.connection_string = config_entry.data.get(CONF_CONNECTION_STRING)
        _opts = config_entry.options
        self._refresh_interval = _opts.get(CONF_REFRESH_INTERVAL, config_entry.data.get(CONF_REFRESH_INTERVAL, 120))
        self._has_feedback_module = _opts.get(CONF_HAS_FEEDBACK_MODULE, config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False))
        self._prior_gen3 = _opts.get(CONF_PRIOR_GEN3, config_entry.data.get(CONF_PRIOR_GEN3, False))

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

        self.nikobus_actuator: NikobusActuator | None = None
        self.nikobus_listener: NikobusEventListener | None = None
        self.nikobus_command: NikobusCommandHandler | None = None
        self.nikobus_discovery: NikobusDiscovery | None = None

        # Shared module state buffer — owned here, passed to NikobusCommandHandler
        self._module_states: dict[str, bytearray] = {}

        self.discovery_running = False
        self.discovery_module = None
        self.discovery_module_address: str | None = None
        self.inventory_query_type: InventoryQueryType | None = None
        self._reload_task = None
        self._stopping: bool = False
        self._reconnect_task: asyncio.Task | None = None
        self._last_connected: datetime | None = None
        self._reconnect_attempts: int = 0

    # ------------------------------------------------------------------
    # Backward-compat property so diagnostics.py and other readers work
    # ------------------------------------------------------------------

    @property
    def nikobus_module_states(self) -> dict[str, bytearray]:
        """Return the shared module state buffer."""
        return self._module_states

    @property
    def connection_status(self) -> str:
        """Return 'connected', 'reconnecting', or 'disconnected'."""
        if self.nikobus_connection.is_connected:
            return "connected"
        if self._reconnect_task and not self._reconnect_task.done():
            return "reconnecting"
        return "disconnected"

    def _get_update_interval(self) -> timedelta | None:
        if self._has_feedback_module or self._prior_gen3:
            return None
        return timedelta(seconds=self._refresh_interval)

    # ------------------------------------------------------------------
    # Connect / setup
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection and initialize all Nikobus components."""
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

            # 1. Create actuator and discovery (needed before listener)
            self.nikobus_actuator = NikobusActuator(
                self.hass, self, self.dict_button_data, self.dict_module_data
            )
            self.nikobus_discovery = NikobusDiscovery(
                self,
                config_dir=self.hass.config.config_dir,
                create_task=self.hass.async_create_task,
            )
            self.nikobus_discovery.on_discovery_finished = self._handle_discovery_finished

            # 2. Create listener with a single event_callback and feedback_callback
            self.nikobus_listener = NikobusEventListener(
                self.nikobus_connection,
                self._event_callback,
                feedback_callback=self._feedback_callback,
                has_feedback_module=self._has_feedback_module,
            )
            self.nikobus_listener.on_connection_lost = self._handle_connection_lost

            # 3. Create command handler — shares the coordinator's state buffer
            self.nikobus_command = NikobusCommandHandler(
                self.nikobus_connection,
                self.nikobus_listener,
                module_states=self._module_states,
            )
            self._initialize_module_states()

            # 4. Create the high-level API
            self.api = NikobusAPI(self.nikobus_command, self.dict_module_data)

            await self.nikobus_command.start()
            await self.nikobus_listener.start()
            self._last_connected = datetime.now(timezone.utc)

        except NikobusDataError:
            raise
        except Exception as err:
            _LOGGER.exception("Failed to initialize Nikobus components: %s", err)
            raise HomeAssistantError(f"Initialization error: {err}") from err

    def _initialize_module_states(self) -> None:
        """Pre-allocate state buffers for all configured modules."""
        for modules in self.dict_module_data.values():
            module_items = modules.items() if isinstance(modules, dict) else (
                (m.get("address"), m) for m in modules if isinstance(m, dict)
            )
            for address, info in module_items:
                if address:
                    addr_upper = str(address).upper()
                    if addr_upper not in self._module_states:
                        self._module_states[addr_upper] = bytearray(12)

    # ------------------------------------------------------------------
    # Listener callbacks
    # ------------------------------------------------------------------

    async def _event_callback(self, message: str) -> None:
        """Route non-feedback bus events (buttons, ACKs, discovery frames)."""
        if message.startswith("#N"):
            # Extract the 6-char address after the "#N" prefix
            if self.nikobus_actuator and len(message) >= 8:
                await self.nikobus_actuator.handle_button_press(message[2:8])
        elif message.startswith(DEVICE_ADDRESS_INVENTORY):
            # $18 inventory frame — only reaches here if library forwards it
            await self._inventory_callback(message, self.discovery_running)
        elif any(message.startswith(p) for p in DEVICE_INVENTORY_ANSWER):
            # $2E/$1E discovery response — only reaches here if library forwards it
            await self._discovery_frame_callback(message)

    async def _feedback_callback(self, group: int, message: str) -> None:
        """Process a $1C feedback frame: update state buffer + fire HA event."""
        try:
            # $1C frame format: $1C<addr_lo><addr_hi><crc16_2bytes><state_12hex><crc8_2bytes>
            # address bytes at [3:7], byte-swapped; state at [9:21]
            addr_raw = message[3:7]
            address = (addr_raw[2:] + addr_raw[:2]).upper()

            if len(message) >= 21:
                state_hex = message[9:21]
                start = 0 if group == 1 else 6
                buf = self._module_states.get(address)
                if buf is None:
                    # Auto-allocate if module wasn't pre-registered
                    buf = bytearray(12)
                    self._module_states[address] = buf
                state_bytes = bytes.fromhex(state_hex)
                buf[start : start + 6] = state_bytes

                # Resolve any pending get_output_state future immediately
                if self.nikobus_command:
                    self.nikobus_command.resolve_pending_get(address, group, state_hex)

            await self.async_event_handler(
                "nikobus_refreshed",
                {"impacted_module_address": address, "impacted_module_group": group},
            )
        except Exception as err:
            _LOGGER.error("Feedback callback error: %s", err)

    async def _inventory_callback(self, message: str, discovery_active: bool) -> None:
        """Route $18 inventory frames."""
        if not self.nikobus_discovery:
            return
        if discovery_active:
            if self.inventory_query_type == InventoryQueryType.PC_LINK:
                self.nikobus_discovery.handle_device_address_inventory(message)
            else:
                await self.nikobus_discovery.query_module_inventory(message[3:7])
        elif hasattr(self.nikobus_discovery, "process_mode_button_press"):
            await self.nikobus_discovery.process_mode_button_press(message)

    async def _discovery_frame_callback(self, message: str) -> None:
        """Route $2E/$1E discovery response frames."""
        if not self.nikobus_discovery:
            return
        if self.inventory_query_type == InventoryQueryType.MODULE:
            await self.nikobus_discovery.parse_module_inventory_response(message)
        else:
            await self.nikobus_discovery.parse_inventory_response(message)

    # ------------------------------------------------------------------
    # Data update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> None:
        """Refresh latest data from the Nikobus system via polling."""
        if self.discovery_running:
            return None
        try:
            for module_type in MODULE_TYPES:
                if module_type in self.dict_module_data:
                    await self._refresh_module_type(self.dict_module_data[module_type])
            return None
        except NikobusDataError as err:
            _LOGGER.error("Error fetching Nikobus data: %s", err)
            raise UpdateFailed(f"Data refresh failed: {err}") from err

    async def _refresh_module_type(self, modules_dict: dict[str, Any]) -> None:
        """Poll the bus for each module address in a collection."""
        for address, module_data in modules_dict.items():
            normalized = str(address).upper()
            channels = module_data.get("channels", [])
            chan_count = len(channels)
            groups = (1,) if chan_count <= 6 else (1, 2)

            for g in groups:
                try:
                    state_hex = await self.nikobus_command.get_output_state(normalized, g) or ""
                    if state_hex and len(state_hex) >= 12:
                        start = 0 if g == 1 else 6
                        buf = self._module_states.get(normalized)
                        if buf is None:
                            buf = bytearray(12)
                            self._module_states[normalized] = buf
                        buf[start : start + 6] = bytes.fromhex(state_hex[:12])
                except Exception as err:
                    _LOGGER.error("Error refreshing %s group %d: %s", normalized, g, err)

            await self.async_event_handler(
                "nikobus_refreshed",
                {"impacted_module_address": normalized},
            )

    # ------------------------------------------------------------------
    # State buffer accessors (delegate to library or direct buffer access)
    # ------------------------------------------------------------------

    @callback
    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Return raw byte state for a channel (1-based)."""
        buf = self._module_states.get(address.upper())
        if buf and 0 < channel <= len(buf):
            return buf[channel - 1]
        return 0

    @callback
    def get_bytearray_group_state(self, address: str, group: int) -> bytearray:
        """Return 6-byte group state."""
        if self.nikobus_command:
            return self.nikobus_command.get_bytearray_group_state(address, int(group))
        return bytearray(6)

    @callback
    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Update a single channel in the state buffer."""
        if self.nikobus_command:
            self.nikobus_command.set_bytearray_state(address, channel, value)

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update a group in the state buffer from a hex string."""
        addr_upper = address.upper()
        buf = self._module_states.get(addr_upper)
        if buf is None:
            return
        start = 0 if int(group) == 1 else 6
        try:
            state_bytes = bytes.fromhex(value[:12].ljust(12, "0"))
            buf[start : start + 6] = state_bytes[:6]
        except (ValueError, IndexError):
            pass

    # ------------------------------------------------------------------
    # Module metadata helpers
    # ------------------------------------------------------------------

    def get_module_channel_count(self, module_id: str) -> int:
        """Return the channel count for a module (from config)."""
        for modules in self.dict_module_data.values():
            if data := modules.get(module_id):
                return len(data.get("channels", []))
        return 0

    def get_module_type(self, module_id: str) -> str | None:
        """Return the hardware type of the specified module."""
        for m_type, modules in self.dict_module_data.items():
            if module_id in modules:
                return m_type
        return None

    def get_cover_operation_time(
        self, module_id: str, channel: int, direction: str = "up", default: float = 30.0
    ) -> float:
        """Fetch travel time for a shutter channel."""
        try:
            ch = self.dict_module_data.get("roller_module", {}).get(module_id, {}).get(
                "channels", []
            )[int(channel) - 1]
            ot = ch.get(f"operation_time_{direction}")
            return float(ot) if ot and float(ot) > 0 else default
        except (IndexError, ValueError, KeyError):
            return default

    # ------------------------------------------------------------------
    # Convenience state accessors used by entity platforms
    # ------------------------------------------------------------------

    def get_light_brightness(self, addr: str, ch: int) -> int:
        return self.get_bytearray_state(addr, ch)

    def get_switch_state(self, addr: str, ch: int) -> bool:
        return self.get_bytearray_state(addr, ch) == 0xFF

    def get_cover_state(self, addr: str, ch: int) -> int:
        return self.get_bytearray_state(addr, ch)

    # ------------------------------------------------------------------
    # Event / dispatcher
    # ------------------------------------------------------------------

    async def async_event_handler(self, event: str, data: dict[str, Any]) -> None:
        """Dispatch events and trigger targeted entity updates."""
        if event == "ha_button_pressed":
            await self.nikobus_command.queue_command(f"#N{data.get('address')}\r#E1")

        if address := data.get("impacted_module_address"):
            async_dispatcher_send(self.hass, f"{DOMAIN}_update_{address}")
        else:
            _LOGGER.debug("Global broadcast refresh triggered")
            self.async_update_listeners()

    # ------------------------------------------------------------------
    # Connection lost / reconnect
    # ------------------------------------------------------------------

    async def _handle_connection_lost(self) -> None:
        """Called by the listener when the connection drops."""
        if self._stopping:
            return
        _LOGGER.warning("Nikobus connection lost — scheduling reconnect.")
        self.async_update_listeners()
        if self.nikobus_command:
            await self.nikobus_command.stop()
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_background_task(
                self._reconnect_loop(), name="nikobus_reconnect"
            )

    async def _reconnect_loop(self) -> None:
        """Exponential back-off reconnect loop."""
        delay = RECONNECT_DELAY_INITIAL
        attempt = 0
        while not self._stopping:
            attempt += 1
            self._reconnect_attempts += 1
            _LOGGER.info("Nikobus reconnect attempt %d (delay %ds)…", attempt, delay)
            self.async_update_listeners()
            try:
                await self.nikobus_connection.connect()
            except Exception as err:
                _LOGGER.warning("Reconnect %d failed: %s — retrying in %ds", attempt, err, delay)
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                delay = min(delay * 2, RECONNECT_DELAY_MAX)
                continue

            try:
                # Drain stale queue entries and reset listener state
                while not self.nikobus_command._command_queue.empty():
                    try:
                        self.nikobus_command._command_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                self.nikobus_command._queued_get_keys.clear()

                self.nikobus_listener._frame_buffer = ""
                self.nikobus_listener._last_query_group.clear()
                while not self.nikobus_listener.response_queue.empty():
                    try:
                        self.nikobus_listener.response_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                await self.nikobus_command.start()
                self.nikobus_listener.on_connection_lost = self._handle_connection_lost
                await self.nikobus_listener.start()
                self._last_connected = datetime.now(timezone.utc)
                self._reconnect_attempts = 0
                await self._async_update_data()
                self.async_update_listeners()
                _LOGGER.info("Nikobus reconnected after %d attempt(s).", attempt)
                return
            except Exception as err:
                _LOGGER.error("Subsystem restart failed after reconnect: %s — retrying", err)
                await self.nikobus_connection.disconnect()
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Shut down background tasks and disconnect."""
        self._stopping = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        if self._reload_task and not self._reload_task.done():
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None
        try:
            if self.nikobus_listener:
                await self.nikobus_listener.stop()
        except Exception as err:
            _LOGGER.error("Error stopping listener: %s", err)
        try:
            if self.nikobus_command:
                await self.nikobus_command.stop()
        except Exception as err:
            _LOGGER.error("Error stopping command handler: %s", err)
        try:
            await self.nikobus_connection.disconnect()
        except Exception as err:
            _LOGGER.error("Error disconnecting: %s", err)

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def get_known_entity_unique_ids(self) -> set[str]:
        """Return the set of valid unique_ids for all Nikobus entities."""
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
        known.add(f"{DOMAIN}_connection_status")
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
        self.discovery_running = False
        if self._reload_task and not self._reload_task.done():
            return

        async def _reload() -> None:
            try:
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            except Exception as err:
                _LOGGER.error("Failed to reload config entry after discovery: %s", err)

        self._reload_task = self.hass.async_create_task(_reload())
