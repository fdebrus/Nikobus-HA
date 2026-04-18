"""Coordinator for Nikobus integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from nikobus_connect import NikobusAPI, NikobusCommandHandler, NikobusConnect, NikobusEventListener
from nikobus_connect.discovery import NikobusDiscovery, InventoryQueryType
from nikobus_connect.exceptions import NikobusConnectionError, NikobusDataError, NikobusError

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY_ANSWER,
    DISCOVERY_PHASE_ERROR,
    DISCOVERY_PHASE_FINISHED,
    DISCOVERY_PHASE_IDLE,
    DISCOVERY_PHASE_MODULE_SCAN,
    DISCOVERY_PHASE_PC_LINK,
    DOMAIN,
    ISSUE_NO_BUTTONS_CONFIGURED,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    SIGNAL_DISCOVERY_STATE,
)
from .nkbactuator import NikobusActuator
from .nkbconfig import NikobusConfig

# Typed config entry alias used across the integration. A plain alias
# (instead of PEP 695 `type X = ...`) keeps compatibility with older
# HA Python versions.
NikobusConfigEntry = ConfigEntry["NikobusDataCoordinator"]

_LOGGER = logging.getLogger(__name__)

# Module types supported for polling
MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")

# After finding real device data in the PC Link registry, abort the scan
# once this many consecutive empty (0xFF) blocks are encountered.
_DISCOVERY_EMPTY_THRESHOLD = 3


class NikobusDataCoordinator(DataUpdateCoordinator[None]):
    """Coordinator for managing asynchronous updates and connections to Nikobus."""

    config_entry: NikobusConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: NikobusConfigEntry) -> None:
        """Initialize the coordinator."""
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
            config_entry=config_entry,
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
        self._discovery_found_data: bool = False
        self._consecutive_empty_blocks: int = 0
        self._reload_task = None

        # --- Discovery progress tracking (for UI) ---
        self.discovery_phase: str = DISCOVERY_PHASE_IDLE
        self.discovery_status_message: str = ""
        self.discovery_current_module: str | None = None
        self.discovery_modules_done: int = 0
        self.discovery_modules_total: int = 0
        self.discovery_registers_done: int = 0
        self.discovery_registers_total: int = 0
        self.discovery_last_error: str | None = None
        self._discovery_finished_event: asyncio.Event = asyncio.Event()
        self._discovery_finished_event.set()  # idle = already set
        self._discovery_auto_reload: bool = True
        self._discovery_progress_task: asyncio.Task | None = None
        self._discovery_module_order: list[str] = []
        self._module_scan_frame_count: int = 0
        self._module_scan_last_index: int = -1
        # Monkey-patch state for counting commands sent during discovery
        self._original_send_command = None
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

    def reset_discovery_counters(self) -> None:
        """Clear the empty-block / found-data counters before a new scan."""
        self._discovery_found_data = False
        self._consecutive_empty_blocks = 0

    def refresh_repair_issues(self) -> None:
        """Create / clear repair issues based on the current configuration."""
        has_buttons = bool(
            self.dict_button_data.get("nikobus_button")
        )
        issue_id = f"{ISSUE_NO_BUTTONS_CONFIGURED}_{self.config_entry.entry_id}"
        if has_buttons:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NO_BUTTONS_CONFIGURED,
            data={"entry_id": self.config_entry.entry_id},
        )

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
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception("Failed to initialize Nikobus components")
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="initialization_error",
                translation_placeholders={"error": str(err)},
            ) from err

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
        except asyncio.CancelledError:
            raise
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
        if not self.nikobus_discovery or not self.discovery_running:
            return
        if self.inventory_query_type == InventoryQueryType.MODULE:
            await self.nikobus_discovery.parse_module_inventory_response(message)
            # Per-command progress is counted by the wrapped _send_command
            # (see _install_command_counter); we just refresh the UI state.
            self._update_module_scan_progress()
            return

        # PC Link inventory response
        await self.nikobus_discovery.parse_inventory_response(message)

        # Count this frame toward the PC Link progress bar.
        self.discovery_registers_done = min(
            self.discovery_registers_total,
            self.discovery_registers_done + 1,
        )
        devices_found = len(getattr(self.nikobus_discovery, "discovered_devices", {}) or {})

        # Early termination: stop scanning after consecutive empty registry blocks.
        if self._is_empty_inventory_block(message):
            if self._discovery_found_data:
                self._consecutive_empty_blocks += 1
                if self._consecutive_empty_blocks >= _DISCOVERY_EMPTY_THRESHOLD:
                    _LOGGER.info(
                        "PC Link inventory: %d consecutive empty blocks — stopping early",
                        self._consecutive_empty_blocks,
                    )
                    await self._abort_discovery_early()
                    return
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_PC_LINK,
                message=(
                    f"PC Link inventory: {self.discovery_registers_done}/"
                    f"{self.discovery_registers_total} registers, "
                    f"{devices_found} device(s) found"
                ),
            )
        else:
            self._discovery_found_data = True
            self._consecutive_empty_blocks = 0
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_PC_LINK,
                message=(
                    f"PC Link inventory: {self.discovery_registers_done}/"
                    f"{self.discovery_registers_total} registers, "
                    f"{devices_found} device(s) found"
                ),
            )

    def _update_module_scan_progress(self) -> None:
        """Refresh the module-scan progress state for the UI.

        Uses the pre-captured ``_discovery_module_order`` list together
        with the library's remaining-queue length to determine the current
        module. Per-module register progress is a monotonic counter of the
        response frames we have received for the current module (reset on
        module transition).
        """
        disc = self.nikobus_discovery
        if disc is None:
            return

        if self.discovery_registers_total == 0:
            self.discovery_registers_total = 240  # 0x10-0xFF

        total = self.discovery_modules_total or len(self._discovery_module_order)
        if total == 0:
            async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)
            return

        queue_list = getattr(disc, "_register_scan_queue", None)
        remaining = len(queue_list) if isinstance(queue_list, list) else 0

        # How many modules have been POPPED so far (including the current one).
        popped = total - remaining
        current_index_0 = max(0, popped - 1)  # 0-based index of current module
        current_index_1 = min(current_index_0 + 1, total)  # 1-based display
        self.discovery_modules_done = current_index_0

        # Reset per-module frame counter when we advance to a new module.
        if current_index_0 != self._module_scan_last_index:
            self._module_scan_last_index = current_index_0
            self._module_scan_frame_count = 0

        if self._discovery_module_order and current_index_0 < len(
            self._discovery_module_order
        ):
            self.discovery_current_module = self._discovery_module_order[
                current_index_0
            ]
        else:
            lib_current = (
                getattr(disc, "_module_address", None)
                or self.discovery_module_address
            )
            if lib_current:
                self.discovery_current_module = lib_current

        self.discovery_registers_done = min(
            self.discovery_registers_total,
            self._module_scan_frame_count,
        )

        if self.discovery_current_module:
            self._update_discovery_state(
                message=(
                    f"Scanning module {self.discovery_current_module} "
                    f"({current_index_1}/{total}) — "
                    f"{self.discovery_registers_done}/{self.discovery_registers_total}"
                ),
            )
        else:
            async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)

    def _install_command_counter(self) -> None:
        """Wrap the command handler's _send_command to count discovery commands.

        Every inventory register query results in exactly one _send_command
        invocation (for commands without an address, i.e., fire-and-forget).
        Wrapping that method gives us a reliable per-command counter that
        doesn't depend on bus frame batching.
        """
        handler = self.nikobus_command
        if handler is None:
            _LOGGER.debug("Command counter install skipped: no handler")
            return
        if self._original_send_command is not None:
            _LOGGER.debug("Command counter install skipped: already installed")
            return
        original = handler._send_command
        self._original_send_command = original
        coordinator = self  # closure reference

        async def _counting_send_command(*args, **kwargs):
            result = await original(*args, **kwargs)
            if (
                coordinator.discovery_running
                and coordinator.inventory_query_type == InventoryQueryType.MODULE
            ):
                coordinator._module_scan_frame_count = min(
                    coordinator.discovery_registers_total or 240,
                    coordinator._module_scan_frame_count + 1,
                )
            return result

        handler._send_command = _counting_send_command
        _LOGGER.debug("Command counter installed on handler %s", handler)

    def _uninstall_command_counter(self) -> None:
        """Restore the original _send_command method."""
        handler = self.nikobus_command
        if handler is None or self._original_send_command is None:
            return
        handler._send_command = self._original_send_command
        self._original_send_command = None

    def _start_progress_poller(self) -> None:
        """Start a background task that polls progress once per second."""
        if self._discovery_progress_task and not self._discovery_progress_task.done():
            return
        self._discovery_progress_task = self.hass.async_create_task(
            self._progress_poll_loop()
        )

    def _stop_progress_poller(self) -> None:
        """Cancel the background progress poller."""
        task = self._discovery_progress_task
        self._discovery_progress_task = None
        if task and not task.done():
            task.cancel()

    async def _progress_poll_loop(self) -> None:
        """Periodically refresh module-scan progress so the UI sees updates.

        Frame callbacks alone are not enough to drive live updates because
        the library buffers multiple chunks per frame and empty registers
        don't always produce a frame. This loop re-reads the command queue
        every second and publishes a fresh state message.
        """
        try:
            while self.discovery_running:
                if self.inventory_query_type == InventoryQueryType.MODULE:
                    self._update_module_scan_progress()
                else:
                    async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Discovery progress poller error: %s", err)

    @staticmethod
    def _is_empty_inventory_block(message: str) -> bool:
        """Check whether a $2E/$1E inventory response carries no device data.

        The first data byte after the PC-Link address indicates the record
        type (0x03 = module, 0x04+ = button).  A value of 0xFF means the
        registry slot is unused.
        """
        return len(message) < 9 or message[7:9].upper() == "FF"

    async def _abort_discovery_early(self) -> None:
        """Stop the PC-Link inventory scan ahead of schedule."""
        # Drain remaining discovery commands so they are not sent on the bus.
        if self.nikobus_command and hasattr(self.nikobus_command, "_command_queue"):
            drained = 0
            while not self.nikobus_command._command_queue.empty():
                try:
                    self.nikobus_command._command_queue.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if drained:
                _LOGGER.debug("Drained %d queued discovery commands", drained)
        await self._handle_discovery_finished()

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
                except asyncio.CancelledError:
                    raise
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

    def get_button_channels(self, button_address: str) -> int | None:
        """Return the operation-point count for a physical button address.

        Called by nikobus-connect decoders to derive the push-button (bus)
        address from the physical device address found in module firmware.
        Looks up ``linked_button[].address`` across all button entries.
        """
        normalized = (button_address or "").upper()
        for button in (self.dict_button_data or {}).get("nikobus_button", {}).values():
            for info in button.get("linked_button") or []:
                if isinstance(info, dict) and (info.get("address") or "").upper() == normalized:
                    ch = info.get("channels")
                    if isinstance(ch, int) and ch > 0:
                        return ch
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
        _LOGGER.warning("Nikobus connection lost — scheduling reconnect")
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
            except asyncio.CancelledError:
                raise
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
                _LOGGER.info("Nikobus reconnected after %d attempt(s)", attempt)
                return
            except asyncio.CancelledError:
                raise
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
        """Shut down background tasks and disconnect.

        Cancels background tasks first so no in-flight handler can touch
        the listener, command handler, or connection while we tear them
        down. Then stops the protocol stack in the reverse of the order
        it was started.
        """
        self._stopping = True

        # 1. Cancel background tasks FIRST.
        for task_attr in ("_reconnect_task", "_reload_task"):
            task: asyncio.Task | None = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            setattr(self, task_attr, None)

        # 2. Then stop subsystems in reverse start order.
        if self.nikobus_listener:
            try:
                await self.nikobus_listener.stop()
            except NikobusError as err:
                _LOGGER.error("Error stopping listener: %s", err)
        if self.nikobus_command:
            try:
                await self.nikobus_command.stop()
            except NikobusError as err:
                _LOGGER.error("Error stopping command handler: %s", err)
        try:
            await self.nikobus_connection.disconnect()
        except NikobusError as err:
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
        known.add(f"{DOMAIN}_discovery_status")
        known.add(f"{DOMAIN}_discovery_progress")
        known.add(f"{DOMAIN}_pc_link_inventory_button")
        known.add(f"{DOMAIN}_module_scan_button")
        return known

    def _merge_discovered_modules(self, discovered: dict[str, Any]) -> None:
        """Integrate newly discovered hardware into the registry."""
        for m_type, modules in discovered.items():
            target = self.dict_module_data.setdefault(m_type, {})
            for addr, info in modules.items():
                if addr not in target:
                    target[addr] = info

    async def _handle_discovery_finished(self) -> None:
        """Signal discovery completion; optionally reload the config entry."""
        self.discovery_running = False
        self._stop_progress_poller()
        self._uninstall_command_counter()
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_FINISHED,
            message="Discovery finished",
        )
        self._discovery_finished_event.set()

        # Skip the auto-reload when the options flow triggered the discovery;
        # the flow will reload via its final async_create_entry call.
        if not self._discovery_auto_reload:
            return

        if self._reload_task and not self._reload_task.done():
            return

        async def _reload() -> None:
            try:
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.error("Failed to reload config entry after discovery: %s", err)

        self._reload_task = self.hass.async_create_task(_reload())

    # ------------------------------------------------------------------
    # Discovery progress API (called by options flow / buttons / sensors)
    # ------------------------------------------------------------------

    @property
    def discovery_progress_percent(self) -> int:
        """Overall progress estimate (0-100) for the current discovery phase."""
        if self.discovery_phase == DISCOVERY_PHASE_IDLE:
            return 0
        if self.discovery_phase == DISCOVERY_PHASE_FINISHED:
            return 100
        if self.discovery_phase == DISCOVERY_PHASE_PC_LINK:
            if self.discovery_registers_total:
                pct = int(
                    (self.discovery_registers_done / self.discovery_registers_total) * 100
                )
                return min(99, pct)
            return 10
        if self.discovery_phase == DISCOVERY_PHASE_MODULE_SCAN:
            total = self.discovery_modules_total or 1
            done = self.discovery_modules_done
            per_module = 0
            if self.discovery_registers_total:
                per_module = self.discovery_registers_done / self.discovery_registers_total
            return min(99, int(((done + per_module) / total) * 100))
        return 0

    def _update_discovery_state(
        self,
        *,
        phase: str | None = None,
        message: str | None = None,
        current_module: str | None = None,
        modules_done: int | None = None,
        modules_total: int | None = None,
        registers_done: int | None = None,
        registers_total: int | None = None,
        error: str | None = None,
    ) -> None:
        """Update discovery progress and notify listeners."""
        if phase is not None:
            self.discovery_phase = phase
        if message is not None:
            self.discovery_status_message = message
        if current_module is not None:
            self.discovery_current_module = current_module
        if modules_done is not None:
            self.discovery_modules_done = modules_done
        if modules_total is not None:
            self.discovery_modules_total = modules_total
        if registers_done is not None:
            self.discovery_registers_done = registers_done
        if registers_total is not None:
            self.discovery_registers_total = registers_total
        if error is not None:
            self.discovery_last_error = error
        async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)

    async def start_pc_link_inventory(self, *, auto_reload: bool = True) -> None:
        """Run a PC Link inventory discovery and wait until it completes."""
        if not self.nikobus_discovery:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="discovery_not_initialized",
            )
        if self.discovery_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="discovery_already_running",
            )
        self._discovery_found_data = False
        self._consecutive_empty_blocks = 0
        self._discovery_auto_reload = auto_reload
        self._discovery_finished_event.clear()
        # PC Link inventory scans register range 0xA4-0xFF = 92 frames.
        # This is a rough total; early termination may stop the scan sooner.
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_PC_LINK,
            message="Scanning PC Link registry for modules and buttons…",
            current_module=None,
            modules_done=0,
            modules_total=0,
            registers_done=0,
            registers_total=92,
            error=None,
        )
        self._start_progress_poller()
        try:
            await self.nikobus_discovery.start_inventory_discovery()
            # The library returns after queueing commands; wait for the
            # on_discovery_finished callback to actually fire.
            await self._discovery_finished_event.wait()
        except asyncio.CancelledError:
            self._stop_progress_poller()
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
        except Exception as err:
            self._stop_progress_poller()
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_ERROR,
                message=f"PC Link inventory failed: {err}",
                error=str(err),
            )
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise

    async def start_module_scan(
        self,
        module_address: str | None = None,
        *,
        auto_reload: bool = True,
    ) -> None:
        """Run module inventory discovery and wait until it completes."""
        if not self.nikobus_discovery:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="discovery_not_initialized",
            )
        if self.discovery_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="discovery_already_running",
            )

        if module_address:
            target = module_address.strip().upper()
            total = 1
            message = f"Scanning module {target}…"
            self._discovery_module_order = [target]
        else:
            target = "ALL"
            # Count configured output modules for progress display.
            self._discovery_module_order = []
            for m_type, modules in self.dict_module_data.items():
                if m_type in ("pc_link", "pc_logic", "feedback_module", "other_module"):
                    continue
                if isinstance(modules, dict):
                    self._discovery_module_order.extend(
                        str(addr).upper() for addr in modules.keys()
                    )
            total = len(self._discovery_module_order)
            message = f"Scanning {total} modules…" if total else "Scanning modules…"

        self._discovery_auto_reload = auto_reload
        self._discovery_finished_event.clear()
        self._module_scan_frame_count = 0
        # Initialize to 0 so the first poll tick (current_index_0 == 0)
        # does not reset the counter and discard the initial increments.
        self._module_scan_last_index = 0
        self._install_command_counter()
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_MODULE_SCAN,
            message=message,
            current_module=None if target == "ALL" else target,
            modules_done=0,
            modules_total=total,
            registers_done=0,
            registers_total=0,
            error=None,
        )
        self._start_progress_poller()
        try:
            await self.nikobus_discovery.query_module_inventory(target)
            # The library returns after queueing commands; wait for the
            # on_discovery_finished callback to actually fire.
            await self._discovery_finished_event.wait()
        except asyncio.CancelledError:
            self._stop_progress_poller()
            self._uninstall_command_counter()
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
        except Exception as err:
            self._stop_progress_poller()
            self._uninstall_command_counter()
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_ERROR,
                message=f"Module scan failed: {err}",
                error=str(err),
            )
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
