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
from nikobus_connect.discovery import (
    NikobusDiscovery,
    InventoryQueryType,
    find_module,
    find_operation_point,
)
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
    DISCOVERY_SUB_PHASE_ERROR,
    DISCOVERY_SUB_PHASE_FINALIZING,
    DISCOVERY_SUB_PHASE_FINISHED,
    DISCOVERY_SUB_PHASE_IDENTITY,
    DISCOVERY_SUB_PHASE_IDLE,
    DISCOVERY_SUB_PHASE_INVENTORY,
    DISCOVERY_SUB_PHASE_REGISTER_SCAN,
    DISCOVERY_WEIGHT_FINALIZING,
    DISCOVERY_WEIGHT_IDENTITY,
    DISCOVERY_WEIGHT_INVENTORY,
    DISCOVERY_WEIGHT_REGISTER_SCAN,
    DOMAIN,
    ISSUE_NO_BUTTONS_CONFIGURED,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    SIGNAL_DISCOVERY_STATE,
)
from .nkbactuator import NikobusActuator
from .nkbconfig import NikobusConfig
from .nkbmigration import async_migrate_legacy_module_config
from .nkbstorage import NikobusButtonStorage, NikobusModuleStorage

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
        self.button_storage = NikobusButtonStorage(hass)
        self.module_storage = NikobusModuleStorage(hass)
        self.api: NikobusAPI | None = None

        # ``dict_module_data`` is a derived view of ``module_storage.data``,
        # grouped by ``module_type`` for the library's scan planner and for
        # the router/actuator/polling code. It is rebuilt after every load
        # or save via ``_rebuild_dict_module_data``.
        self.dict_module_data: dict[str, Any] = {}
        self.dict_button_data: dict[str, Any] = {"nikobus_button": {}}
        self.dict_scene_data: dict[str, Any] = {}

        # Lazy cache: (module_address_upper, channel) -> [button records that trigger it]
        self._controlled_by_index: dict[tuple[str, int], list[dict[str, Any]]] | None = None

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
        # `discovery_phase` stays on the legacy enum for backward-compat with
        # automations; `discovery_sub_phase` carries the fine-grained state
        # the library emits via its on_progress callback (0.3.5+).
        self.discovery_phase: str = DISCOVERY_PHASE_IDLE
        self.discovery_sub_phase: str = DISCOVERY_SUB_PHASE_IDLE
        self.discovery_status_message: str = ""
        self.discovery_current_module: str | None = None
        self.discovery_modules_done: int = 0
        self.discovery_modules_total: int = 0
        self.discovery_registers_done: int = 0
        self.discovery_registers_total: int = 0
        self.discovery_register_current: int | None = None
        self.discovery_decoded_records: int = 0
        self.discovery_last_error: str | None = None
        self._discovery_finished_event: asyncio.Event = asyncio.Event()
        self._discovery_finished_event.set()  # idle = already set
        self._discovery_auto_reload: bool = True
        self._discovery_module_order: list[str] = []
        self._stopping: bool = False
        self._reconnect_task: asyncio.Task[None] | None = None
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
            # Module data now lives in .storage/nikobus.modules. On first
            # startup after upgrading to 0.4.0, migrate any legacy
            # nikobus_module_config.json into the store, then rename it.
            await self.module_storage.async_load()
            await async_migrate_legacy_module_config(self.hass, self.module_storage)
            self._rebuild_dict_module_data()

            self.dict_button_data = await self.button_storage.async_load()
            self.dict_scene_data = await self.nikobus_config.load_json_data(
                "nikobus_scene_config.json", "scene"
            )

            # 1. Create actuator and discovery (needed before listener)
            self.nikobus_actuator = NikobusActuator(
                self.hass, self, self.dict_button_data, self.module_storage.data
            )
            self.nikobus_discovery = NikobusDiscovery(
                self,
                config_dir=self.hass.config.config_dir,
                create_task=self.hass.async_create_task,
                button_data=self.dict_button_data,
                on_button_save=self.button_storage.async_save,
                module_data=self.module_storage.data,
                on_module_save=self._async_on_module_save,
                on_progress=self._handle_discovery_progress,
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
        _LOGGER.debug(
            "Nikobus press frame: %s  (raw bytes hex: %s)",
            message,
            message.encode().hex(),
        )
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
            # Progress tracking is driven by the library's on_progress
            # callback (0.3.5+). Here we only forward the raw frame.
            await self.nikobus_discovery.parse_module_inventory_response(message)
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

    # ------------------------------------------------------------------
    # Phase-aware progress (consumes nikobus-connect 0.3.5+ on_progress)
    # ------------------------------------------------------------------

    _SUB_TO_LEGACY_PHASE: dict[str, str] = {
        DISCOVERY_SUB_PHASE_IDLE: DISCOVERY_PHASE_IDLE,
        DISCOVERY_SUB_PHASE_INVENTORY: DISCOVERY_PHASE_PC_LINK,
        DISCOVERY_SUB_PHASE_IDENTITY: DISCOVERY_PHASE_PC_LINK,
        DISCOVERY_SUB_PHASE_REGISTER_SCAN: DISCOVERY_PHASE_MODULE_SCAN,
        DISCOVERY_SUB_PHASE_FINALIZING: DISCOVERY_PHASE_MODULE_SCAN,
        DISCOVERY_SUB_PHASE_FINISHED: DISCOVERY_PHASE_FINISHED,
        DISCOVERY_SUB_PHASE_ERROR: DISCOVERY_PHASE_ERROR,
    }

    async def _handle_discovery_progress(self, progress: Any) -> None:
        """Consume a ``DiscoveryProgress`` event emitted by nikobus-connect 0.3.5+.

        ``progress`` is a ``nikobus_connect.discovery.DiscoveryProgress``
        dataclass with: ``phase``, ``module_address``, ``module_index``,
        ``module_total``, ``register``, ``register_total``,
        ``decoded_records``. The library emits one of the four real phases
        (``inventory`` / ``identity`` / ``register_scan`` / ``finalizing``);
        idle / finished / error states are handled by the caller's own
        ``_handle_discovery_finished`` and error-path code.

        Exceptions raised here are swallowed — the library treats this
        callback as fire-and-forget and must not be stalled by UI plumbing.
        """
        try:
            sub_phase = str(getattr(progress, "phase", "") or DISCOVERY_SUB_PHASE_IDLE)
            legacy_phase = self._SUB_TO_LEGACY_PHASE.get(
                sub_phase, self.discovery_phase
            )

            module_address = getattr(progress, "module_address", None)
            module_index = int(getattr(progress, "module_index", 0) or 0)
            module_total = int(getattr(progress, "module_total", 0) or 0)
            register = getattr(progress, "register", None)
            register_total = int(getattr(progress, "register_total", 0) or 0)
            decoded_records = int(getattr(progress, "decoded_records", 0) or 0)

            registers_done = 0
            if register is not None and register_total:
                # Registers start at 0x10, so done-count is (current - 0x10 + 1).
                try:
                    cur = int(register)
                    registers_done = max(0, cur - 0x10 + 1)
                except (TypeError, ValueError):
                    registers_done = 0

            if sub_phase == DISCOVERY_SUB_PHASE_INVENTORY:
                message = "PC Link inventory: enumerating bus addresses…"
            elif sub_phase == DISCOVERY_SUB_PHASE_IDENTITY:
                message = (
                    f"Identifying modules ({module_index}/{module_total})…"
                    if module_total
                    else "Identifying modules…"
                )
            elif sub_phase == DISCOVERY_SUB_PHASE_REGISTER_SCAN:
                if module_address:
                    message = (
                        f"Scanning module {module_address} "
                        f"({module_index}/{module_total}) — "
                        f"register 0x{int(register or 0):02X} of 0xFF "
                        f"({decoded_records} records)"
                    )
                else:
                    message = f"Scanning modules ({module_index}/{module_total})"
            elif sub_phase == DISCOVERY_SUB_PHASE_FINALIZING:
                message = f"Merging {decoded_records} discovered records…"
            else:
                message = self.discovery_status_message

            self.discovery_sub_phase = sub_phase
            self.discovery_decoded_records = decoded_records
            self.discovery_register_current = (
                int(register) if register is not None else None
            )
            self._update_discovery_state(
                phase=legacy_phase,
                message=message,
                current_module=module_address if module_address else None,
                modules_done=max(0, module_index - 1),
                modules_total=module_total,
                registers_done=registers_done,
                registers_total=register_total or 240,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Discovery progress handler error: %s", err)

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
        hit = find_module(self.module_storage.data, module_id)
        if hit is None:
            return 0
        channels = hit[1].get("channels")
        return len(channels) if isinstance(channels, list) else 0

    def get_module_type(self, module_id: str) -> str | None:
        """Return the hardware type of the specified module."""
        hit = find_module(self.module_storage.data, module_id)
        return hit[1].get("module_type") if hit else None

    def get_button_channels(self, button_address: str) -> int | None:
        """Return the operation-point count for a physical button address.

        Called by nikobus-connect decoders to derive the push-button (bus)
        address from the physical device address found in module firmware.
        Looks up the top-level ``channels`` field on the physical entry
        (schema v2).
        """
        normalized = (button_address or "").upper()
        phys = (self.dict_button_data or {}).get("nikobus_button", {}).get(normalized)
        if isinstance(phys, dict):
            ch = phys.get("channels")
            if isinstance(ch, int) and ch > 0:
                return ch
        return None

    # ------------------------------------------------------------------
    # Discovery link helpers (wall button parents + controlled_by index)
    # ------------------------------------------------------------------

    def get_wall_button_info(self, bus_address: str) -> dict[str, Any] | None:
        """Return the physical-button record a soft button (bus address) belongs to.

        Shape: ``{type, model, address, channels, key}``. Returns ``None`` when
        the bus address is not part of any discovered physical button.
        """
        hit = find_operation_point(self.dict_button_data, bus_address)
        if hit is None:
            return None
        physical_addr, key_label, _op_point = hit
        phys = (self.dict_button_data or {}).get("nikobus_button", {}).get(physical_addr)
        if not isinstance(phys, dict):
            return None
        return {
            "type": phys.get("type"),
            "model": phys.get("model"),
            "address": physical_addr,
            "channels": phys.get("channels"),
            "key": key_label,
        }

    def get_button_linked_outputs(self, bus_address: str) -> list[dict[str, Any]]:
        """Return flattened output links for a soft button (bus address).

        Each item: ``{module_address, channel, mode, t1, t2}``.
        """
        hit = find_operation_point(self.dict_button_data, bus_address)
        if hit is None:
            return []
        _physical_addr, _key_label, op_point = hit
        flattened: list[dict[str, Any]] = []
        for link in op_point.get("linked_modules") or []:
            if not isinstance(link, dict):
                continue
            module_address = link.get("module_address")
            for out in link.get("outputs") or []:
                if not isinstance(out, dict):
                    continue
                flattened.append({
                    "module_address": module_address,
                    "channel": out.get("channel"),
                    "mode": out.get("mode"),
                    "t1": out.get("t1"),
                    "t2": out.get("t2"),
                })
        return flattened

    def _build_controlled_by_index(self) -> dict[tuple[str, int], list[dict[str, Any]]]:
        """Build a (module_address_upper, channel) -> list[button record] index."""
        index: dict[tuple[str, int], list[dict[str, Any]]] = {}
        buttons = (self.dict_button_data or {}).get("nikobus_button", {})
        for physical_addr, phys in buttons.items():
            if not isinstance(phys, dict):
                continue
            op_points = phys.get("operation_points") or {}
            if not isinstance(op_points, dict):
                continue
            for key_label, op_point in op_points.items():
                if not isinstance(op_point, dict):
                    continue
                bus_addr = op_point.get("bus_address") or ""
                description = op_point.get("description") or f"Button {bus_addr}"
                for link in op_point.get("linked_modules") or []:
                    if not isinstance(link, dict):
                        continue
                    module_address = link.get("module_address")
                    if not module_address:
                        continue
                    module_key = str(module_address).upper()
                    for out in link.get("outputs") or []:
                        if not isinstance(out, dict):
                            continue
                        channel = out.get("channel")
                        if not isinstance(channel, int):
                            continue
                        index.setdefault((module_key, channel), []).append({
                            "bus_address": bus_addr,
                            "description": description,
                            "mode": out.get("mode"),
                            "t1": out.get("t1"),
                            "t2": out.get("t2"),
                            "wall_button_address": physical_addr,
                            "wall_button_key": key_label,
                        })
        return index

    def get_controlled_by(self, module_address: str, channel: int) -> list[dict[str, Any]]:
        """Return the buttons that trigger a given ``(module_address, channel)``."""
        if self._controlled_by_index is None:
            self._controlled_by_index = self._build_controlled_by_index()
        return self._controlled_by_index.get(
            (str(module_address).upper(), int(channel)), []
        )

    def invalidate_controlled_by_index(self) -> None:
        """Drop the cached controlled-by index — call after discovery updates."""
        self._controlled_by_index = None

    def get_cover_operation_time(
        self, module_id: str, channel: int, direction: str = "up", default: float = 30.0
    ) -> float:
        """Fetch travel time for a shutter channel."""
        hit = find_module(self.module_storage.data, module_id)
        if hit is None or hit[1].get("module_type") != "roller_module":
            return default
        try:
            ch = hit[1].get("channels", [])[int(channel) - 1]
            ot = ch.get(f"operation_time_{direction}")
            return float(ot) if ot and float(ot) > 0 else default
        except (IndexError, ValueError, KeyError, TypeError):
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
            task: asyncio.Task[None] | None = getattr(self, task_attr, None)
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
        for phys in self.dict_button_data.get("nikobus_button", {}).values():
            if not isinstance(phys, dict):
                continue
            for op_point in (phys.get("operation_points") or {}).values():
                if not isinstance(op_point, dict):
                    continue
                if bus_addr := op_point.get("bus_address"):
                    known.add(f"{DOMAIN}_button_{bus_addr}")
                    known.add(f"{DOMAIN}_push_button_{bus_addr}")
        for scene in self.dict_scene_data.get("scene", []):
            if sid := scene.get("id"):
                known.add(f"{DOMAIN}_scene_{sid}")
        known.add(f"{DOMAIN}_connection_status")
        known.add(f"{DOMAIN}_discovery_status")
        known.add(f"{DOMAIN}_discovery_progress")
        known.add(f"{DOMAIN}_pc_link_inventory_button")
        known.add(f"{DOMAIN}_module_scan_button")
        return known

    def _rebuild_dict_module_data(self) -> None:
        """Regenerate the grouped ``dict_module_data`` view from the Store.

        The Store holds the authoritative flat ``{address: entry}`` dict
        (nikobus-connect 0.4.0 Option-A shape). Many call sites — the
        library's scan planner, ``router.build_routing``, the actuator's
        dimmer check, the polling loop, and ``NikobusAPI._module_data`` —
        still expect the old nested ``{module_type: {address: entry}}``
        shape. Deriving it from the Store keeps a single source of truth.

        Mutates ``self.dict_module_data`` in place so captured references
        (``NikobusAPI``, ``NikobusActuator``) stay valid.
        """
        grouped: dict[str, dict[str, Any]] = {}
        modules = self.module_storage.data.get("nikobus_module") or {}
        if isinstance(modules, dict):
            for address, entry in modules.items():
                if not isinstance(entry, dict):
                    continue
                module_type = entry.get("module_type") or "other_module"
                addr_upper = str(address).upper()
                bucket = grouped.setdefault(module_type, {})
                merged = dict(entry)
                merged.setdefault("address", addr_upper)
                bucket[addr_upper] = merged

        self.dict_module_data.clear()
        self.dict_module_data.update(grouped)

    async def _async_on_module_save(self) -> None:
        """Persist the Store after discovery/user edits, then refresh derived views."""
        await self.module_storage.async_save()
        self._rebuild_dict_module_data()
        # Clear the cached router spec so newly-discovered modules show up.
        domain_data = self.hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(self.config_entry.entry_id, {})
        entry_data.pop("routing", None)

    async def _handle_discovery_finished(self) -> None:
        """Signal discovery completion; optionally reload the config entry."""
        self.discovery_running = False
        self.discovery_sub_phase = DISCOVERY_SUB_PHASE_FINISHED
        self.discovery_register_current = None
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_FINISHED,
            message=(
                f"Discovery finished — {self.discovery_decoded_records} records decoded."
                if self.discovery_decoded_records
                else "Discovery finished"
            ),
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
        """Overall progress estimate (0-100) across all discovery sub-phases.

        Phases are stacked by weight (see const.DISCOVERY_WEIGHT_*):
        inventory → identity → register_scan → finalizing. Within
        register_scan, progress is (completed_modules + partial_current) /
        total_modules.
        """
        if self.discovery_sub_phase in (DISCOVERY_SUB_PHASE_IDLE, DISCOVERY_SUB_PHASE_ERROR):
            return 0
        if self.discovery_sub_phase == DISCOVERY_SUB_PHASE_FINISHED:
            return 100

        # Cumulative floor — everything before the current phase is "done".
        floor = 0
        if self.discovery_sub_phase == DISCOVERY_SUB_PHASE_INVENTORY:
            floor = 0
            phase_weight = DISCOVERY_WEIGHT_INVENTORY
            phase_frac = 0.5  # can't measure inventory fraction directly
        elif self.discovery_sub_phase == DISCOVERY_SUB_PHASE_IDENTITY:
            floor = DISCOVERY_WEIGHT_INVENTORY
            phase_weight = DISCOVERY_WEIGHT_IDENTITY
            total = self.discovery_modules_total or 1
            done = self.discovery_modules_done
            phase_frac = min(1.0, done / total)
        elif self.discovery_sub_phase == DISCOVERY_SUB_PHASE_REGISTER_SCAN:
            floor = DISCOVERY_WEIGHT_INVENTORY + DISCOVERY_WEIGHT_IDENTITY
            phase_weight = DISCOVERY_WEIGHT_REGISTER_SCAN
            total = self.discovery_modules_total or 1
            done = self.discovery_modules_done
            per_module = 0.0
            if self.discovery_registers_total:
                per_module = min(
                    1.0,
                    self.discovery_registers_done / self.discovery_registers_total,
                )
            phase_frac = min(1.0, (done + per_module) / total)
        elif self.discovery_sub_phase == DISCOVERY_SUB_PHASE_FINALIZING:
            floor = (
                DISCOVERY_WEIGHT_INVENTORY
                + DISCOVERY_WEIGHT_IDENTITY
                + DISCOVERY_WEIGHT_REGISTER_SCAN
            )
            phase_weight = DISCOVERY_WEIGHT_FINALIZING
            phase_frac = 0.5
        else:
            # Older libraries may still drive the legacy-phase field only.
            if self.discovery_phase == DISCOVERY_PHASE_PC_LINK:
                return 10
            if self.discovery_phase == DISCOVERY_PHASE_MODULE_SCAN:
                return 40
            return 0

        return min(99, int(floor + phase_frac * phase_weight))

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
        try:
            await self.nikobus_discovery.start_inventory_discovery()
            # The library returns after queueing commands; wait for the
            # on_discovery_finished callback to actually fire.
            await self._discovery_finished_event.wait()
        except asyncio.CancelledError:
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
        except Exception as err:
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
        self.discovery_decoded_records = 0
        self.discovery_register_current = None
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_MODULE_SCAN,
            message=message,
            current_module=None if target == "ALL" else target,
            modules_done=0,
            modules_total=total,
            registers_done=0,
            registers_total=240,
            error=None,
        )
        try:
            await self.nikobus_discovery.query_module_inventory(target)
            # The library returns after queueing commands; wait for the
            # on_discovery_finished callback to actually fire.
            await self._discovery_finished_event.wait()
        except asyncio.CancelledError:
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
        except Exception as err:
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_ERROR,
                message=f"Module scan failed: {err}",
                error=str(err),
            )
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
