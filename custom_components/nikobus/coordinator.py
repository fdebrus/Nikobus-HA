"""Coordinator for Nikobus integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
    CONF_PRESS_REPEAT,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DEFAULT_PRESS_REPEAT,
    PRESS_REPEAT_DELAY,
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
    DISCOVERY_SUB_PHASE_PROBING,
    DISCOVERY_SUB_PHASE_REGISTER_SCAN,
    DISCOVERY_WEIGHT_FINALIZING,
    DISCOVERY_WEIGHT_IDENTITY,
    DISCOVERY_WEIGHT_INVENTORY,
    DISCOVERY_WEIGHT_REGISTER_SCAN,
    DOMAIN,
    INPUT_ONLY_BUTTON_TYPES,
    ISSUE_LEGACY_UNDECODED_BUTTONS,
    ISSUE_NO_BUTTONS_CONFIGURED,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    SIGNAL_DISCOVERY_STATE,
)
from .nkbactuator import NikobusActuator
from .nkbconfig import NikobusConfig
from .nkbmanual import legacy_config_files_present
from .nkbstorage import (
    NikobusButtonStorage,
    NikobusCFStorage,
    NikobusModuleStorage,
)

# Typed config entry alias used across the integration. A plain alias
# (instead of PEP 695 `type X = ...`) keeps compatibility with older
# HA Python versions.
NikobusConfigEntry = ConfigEntry["NikobusDataCoordinator"]

_LOGGER = logging.getLogger(__name__)

# Module types supported for polling
MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")

# Outer-probe parameters passed to nikobus-connect 0.5.20's
# ``detect_stale_inventory(outer_attempts=N, outer_delay=S)``. The library
# handles the loop, dedup, and bus-quiet delay internally — these are just
# the tuning values our IKIKN forensic settled on (issue #319). Each outer
# attempt skips modules already classified ``present`` from a prior pass.
_PROBE_OUTER_ATTEMPTS = 2
_PROBE_OUTER_DELAY_S = 3.0

# nikobus-connect 0.5.22+ tags every decoded output record with a
# ``record_source`` field naming the scan source. ``output_module_table``
# means the link lives in the actual switch/dimmer/roller module's own
# table — current programming, authoritative. The two registry sources
# below are PC-Link / PC-Logic flash registry memory; their records may
# be residue from a previous owner's programming that was never cleared
# by the current owner's DIN-button learn-mode re-pairing.
#
# A button whose EVERY output record is registry-sourced means no output
# module currently knows about it — strong residue signal on installs
# without PC-Logic. On installs WITH PC-Logic, a registry-only button
# could be a legitimate PC-Logic scene trigger; the classifier gates the
# residue verdict on ``_has_pc_logic_module`` to avoid that FP.
_REGISTRY_SOURCES = frozenset({"pc_link_registry", "pc_logic_registry"})


def _member_set_from_outputs(outputs: Any) -> frozenset[tuple[str, int, str]]:
    """Frozenset of ``(module_upper, channel, mode_code)`` for an output
    list — the canonical key for matching a scene/CF by its members,
    used identically on ``.nkb`` groups, CF entries and routing-graph
    op-points so the three are directly comparable."""
    from .nkbnames import _mode_code

    out = set()
    for o in outputs or []:
        if not isinstance(o, dict):
            continue
        mod = o.get("module_address")
        ch = o.get("channel")
        code = _mode_code(o.get("mode"))
        if isinstance(mod, str) and isinstance(ch, int) and code:
            out.add((mod.upper(), ch, code))
    return frozenset(out)


def _cf_member_set(cf: dict[str, Any]) -> frozenset[tuple[str, int, str]]:
    """Member-set key for a stored ``nikobus_cf`` entry."""
    return _member_set_from_outputs((cf or {}).get("outputs"))


def _output_entity_key(unique_id: str | None) -> tuple[str, int] | None:
    """``(MODULE_ADDR_UPPER, channel)`` for a per-channel output entity's
    unique_id (``nikobus_{light|switch|cover}_{kind}_{addr}_{ch}``), else
    ``None`` — used to match ``.nkb`` output names to entities."""
    if not isinstance(unique_id, str):
        return None
    parts = unique_id.split("_")
    if len(parts) < 5 or parts[0] != DOMAIN:
        return None
    if parts[1] not in ("light", "switch", "cover"):
        return None
    addr, ch = parts[-2], parts[-1]
    if not (ch.isdigit() and re.fullmatch(r"[0-9A-Fa-f]+", addr)):
        return None
    return (addr.upper(), int(ch))


def _apply_entity_name(ent_reg: Any, ent: Any, name: str, overwrite: bool) -> bool:
    """Set an entity's name; return True if changed. Non-overwrite only
    fills a blank (no user name set); overwrite replaces any user name."""
    if overwrite:
        if ent.name != name:
            ent_reg.async_update_entity(ent.entity_id, name=name)
            return True
        return False
    if ent.name is None and ent.original_name != name:
        ent_reg.async_update_entity(ent.entity_id, name=name)
        return True
    return False


#: The selectable ``.nkb`` import categories (all applied by default).
NKB_IMPORT_CATEGORIES = ("device_names", "channel_names", "areas", "scenes")


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
        self._press_repeat = _opts.get(CONF_PRESS_REPEAT, config_entry.data.get(CONF_PRESS_REPEAT, DEFAULT_PRESS_REPEAT))

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
        # CF broadcasts persisted across HA restarts. Populated by
        # ``_ingest_cf_broadcasts`` after each discovery completes from
        # the library's ``NikobusDiscovery.discovered_cf_broadcasts``.
        self.cf_storage = NikobusCFStorage(hass)
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
        # The following are written by nikobus-connect, which holds this
        # coordinator as ``self._coordinator`` and assigns these attributes
        # cross-object during discovery. They look unassigned to a grep of
        # THIS file — the writer is in the library — so do NOT remove them:
        # the library both writes and READS them (e.g. ``if not
        # self._coordinator.discovery_module``), and a missing seed here
        # would raise AttributeError if the library reads before its first
        # write.
        #   * ``discovery_module`` / ``discovery_module_address`` — current
        #     per-module register-scan target; read in the library's frame
        #     routing.
        #   * ``inventory_query_type`` — PC_LINK / MODULE phase marker, read
        #     by ``_inventory_callback`` / ``_discovery_frame_callback`` to
        #     route $18 / $2E / $1E frames.
        self.discovery_module = None
        self.discovery_module_address: str | None = None
        self.inventory_query_type: InventoryQueryType | None = None
        self._reload_task = None
        # Was the most recent ``start_module_scan`` a scan-all (every
        # module's register table read)? Set by ``start_module_scan``;
        # read by ``_reconcile_post_discovery`` to decide whether to
        # surface the ``legacy_undecoded_buttons`` Repairs issue.
        # ``legacy_undecoded`` is only a meaningful signal after the
        # decoder has had a chance to read EVERY module's link table —
        # before that, almost every button reads as ``legacy_undecoded``
        # by default (no module's register table has been decoded yet).
        self._last_module_scan_was_full: bool = False

        # Which slice of the full discovery pipeline the running operation
        # covers, so the progress bar can rescale to 0–100 for each
        # standalone button. ``"inventory"`` = Load Project Overview
        # (inventory+identity); ``"module_scan"`` = Load Existing
        # Installation (register-scan+finalizing); ``"full"`` = the whole
        # pipeline (no rescale). See ``discovery_progress_percent``.
        self._discovery_scope: str = "full"

        # --- Discovery progress tracking (for UI) ---
        # `discovery_phase` stays on the legacy enum for backward-compat with
        # automations; `discovery_sub_phase` carries the fine-grained state
        # the library emits via its on_progress callback (0.3.5+).
        self.discovery_phase: str = DISCOVERY_PHASE_IDLE
        self.discovery_sub_phase: str = DISCOVERY_SUB_PHASE_IDLE
        self.discovery_status_message: str = "Idle"
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
        self._pclink_first_response_event: asyncio.Event = asyncio.Event()
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

    @property
    def last_connected(self) -> datetime | None:
        """Timestamp of the last successful connect (UTC), or ``None``.

        Surfaced by the connection sensor's attributes and diagnostics.
        """
        return self._last_connected

    @property
    def reconnect_attempts(self) -> int:
        """Consecutive reconnect attempts since the last successful connect.

        Surfaced by the connection sensor's attributes and diagnostics.
        """
        return self._reconnect_attempts

    def _get_update_interval(self) -> timedelta | None:
        # No poll timer in push mode. A feedback module pushes state
        # unprompted; older PC-Links (prior_gen3) can't sustain the poll
        # cadence, so they run push-only too (button presses + feedback
        # frames drive refreshes). Everything else polls on the interval.
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
            # Module data lives in .storage/nikobus.modules. Boot loads
            # the persisted store as-is; it does not import inventory
            # from the legacy ``nikobus_module_config.json`` /
            # ``nikobus_button_config.json`` files. Those are consulted
            # only by the explicit "Discover modules" action as the
            # PC-Link fallback (see ``start_pc_link_inventory``).
            await self.module_storage.async_load()
            self._rebuild_dict_module_data()

            self.dict_button_data = await self.button_storage.async_load()
            await self.cf_storage.async_load()

            # 3.0.0: the legacy friendly-name overlay (importing entity
            # names from nikobus_module_config.json / nikobus_button_config.json
            # on every boot) has been removed — entity names are managed in
            # Home Assistant and preserved across reloads. The files are still
            # consulted only as the inventory fallback for installs without a
            # PC-Link (start_pc_link_inventory → _apply_manual_inventory_as_fallback).
            # Warn if they're still present so users know the name-import no
            # longer happens.
            await self._warn_if_legacy_config_files_present()

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
                on_module_save=self.async_on_module_save,
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
            "Press frame %s (raw hex %s)",
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
                if start + 6 > len(buf):
                    # Out-of-range group for this module's buffer
                    # (e.g. group 2 on a 6-output module). Drop the
                    # write rather than silently extend the buffer
                    # and promote the module to a larger size.
                    pass
                else:
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
            _LOGGER.error("Feedback callback failed: %s", err)

    async def _inventory_callback(self, message: str, discovery_active: bool) -> None:
        """Route $18 inventory frames."""
        if not self.nikobus_discovery:
            return
        if discovery_active:
            if self.inventory_query_type == InventoryQueryType.PC_LINK:
                self.nikobus_discovery.handle_device_address_inventory(message)
            else:
                await self.nikobus_discovery.query_module_inventory(message[3:7])
        else:
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

        # PC Link inventory response. nikobus-connect 0.5.13+ stops the
        # sweep itself on the first all-FF response (matching what Niko's
        # PC software does), so the previous HA-side
        # "3-consecutive-empty-blocks" early-stop is redundant and was
        # removed. The per-module Stage-2 register scan has its own
        # consecutive-give-up logic in the library and is unrelated.
        # Any PC-Link response proves PC-Link is alive — flag for the
        # step-1 probe so it can stop waiting and commit to the PC-Link
        # path instead of timing out and falling back to manual files.
        self._pclink_first_response_event.set()
        await self.nikobus_discovery.parse_inventory_response(message)

        # Count this frame toward the PC Link progress bar.
        self.discovery_registers_done = min(
            self.discovery_registers_total,
            self.discovery_registers_done + 1,
        )
        # Only own the status message while we're ACTUALLY in the
        # PC-Link inventory sub-phase. ``parse_inventory_response`` also
        # receives the per-register ``$2E`` frames during the library's
        # identity phase (96 reads × N modules), and writing the "PC
        # Link inventory: X/Y" message here would overwrite the
        # "Identifying modules (i/N)…" message that
        # ``_handle_discovery_progress`` just wrote — making the user
        # think discovery is still in inventory long after it's moved on.
        if self.discovery_sub_phase == DISCOVERY_SUB_PHASE_INVENTORY:
            devices_found = len(getattr(self.nikobus_discovery, "discovered_devices", {}) or {})
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_PC_LINK,
                message=(
                    f"PC-Link inventory: {self.discovery_registers_done}/"
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
        DISCOVERY_SUB_PHASE_PROBING: DISCOVERY_PHASE_PC_LINK,
        DISCOVERY_SUB_PHASE_FINISHED: DISCOVERY_PHASE_FINISHED,
        DISCOVERY_SUB_PHASE_ERROR: DISCOVERY_PHASE_ERROR,
    }

    async def _handle_discovery_progress(self, progress: Any) -> None:
        """Consume a ``DiscoveryProgress`` event emitted by nikobus-connect.

        ``progress`` is a ``nikobus_connect.discovery.DiscoveryProgress``
        dataclass. Fields used here:

        - ``phase``: one of ``"inventory"``, ``"identity"``,
          ``"register_scan"``, ``"finalizing"``.
        - ``module_address`` / ``module_index`` / ``module_total``:
          current module + queue position.
        - ``register_total``: cumulative target for the current module
          across all scan passes (0.16.1+). 48 for the vendor plan
          on output modules + PC-Logic; 93 for PC-Link.
        - ``registers_sent``: cumulative count already sent for the
          current module. The right value for the per-module
          progress numerator under the vendor scan plan, since the
          plan reads non-contiguous bytes across multiple passes and
          ``register`` jumps around (e.g. 0x09 → 0x3E → 0x70).
        - ``pass_index`` / ``pass_total``: 1-based pass position
          within the module's plan (e.g. 2/3 for the vendor plan
          link-table pass). 0 outside scans.
        - ``sub_byte``: wire sub-byte of the current pass.
        - ``register``: current byte being read; surfaced for
          diagnostic display but NOT used as the progress numerator.
        - ``decoded_records``: running cumulative across the run.

        Exceptions raised here are swallowed — the library treats this
        callback as fire-and-forget and must not be stalled by UI plumbing.
        """
        try:
            sub_phase = str(getattr(progress, "phase", "") or DISCOVERY_SUB_PHASE_IDLE)
            legacy_phase = self._SUB_TO_LEGACY_PHASE.get(
                sub_phase, self.discovery_phase
            )

            # On sub-phase transition, zero out the register counters
            # so the FIRST emit of the new phase doesn't display the
            # previous phase's totals (e.g. identity's 96/96 leaking
            # into the first register-scan emit). nikobus-connect
            # 0.19.1 also resets these library-side; this is a
            # defence-in-depth so older lib versions get the same
            # behaviour.
            if sub_phase != self.discovery_sub_phase:
                self.discovery_registers_done = 0
                self.discovery_registers_total = 0

            module_address = getattr(progress, "module_address", None)
            module_index = int(getattr(progress, "module_index", 0) or 0)
            module_total = int(getattr(progress, "module_total", 0) or 0)
            register = getattr(progress, "register", None)
            register_total = int(getattr(progress, "register_total", 0) or 0)
            # New 0.16.1 fields — fall back to the legacy (register -
            # 0x10 + 1) calculation when the library doesn't supply
            # them (older lib versions, forensic-mode scans).
            registers_sent = int(getattr(progress, "registers_sent", 0) or 0)
            pass_index = int(getattr(progress, "pass_index", 0) or 0)
            pass_total = int(getattr(progress, "pass_total", 0) or 0)
            sub_byte = getattr(progress, "sub_byte", None)
            decoded_records = int(getattr(progress, "decoded_records", 0) or 0)

            if registers_sent:
                registers_done = registers_sent
            elif register is not None and register_total:
                # Pre-0.16.1 fallback: assume contiguous registers
                # from 0x10. Wrong under the vendor plan but harmless
                # for legacy callers that bypass the plan.
                try:
                    cur = int(register)
                    registers_done = max(0, cur - 0x10 + 1)
                except (TypeError, ValueError):
                    registers_done = 0
            else:
                registers_done = 0

            if sub_phase == DISCOVERY_SUB_PHASE_INVENTORY:
                message = "PC-Link inventory: enumerating bus addresses…"
            elif sub_phase == DISCOVERY_SUB_PHASE_IDENTITY:
                message = (
                    f"Identifying modules ({module_index}/{module_total})…"
                    if module_total
                    else "Identifying modules…"
                )
            elif sub_phase == DISCOVERY_SUB_PHASE_REGISTER_SCAN:
                if module_address:
                    base = (
                        f"Scanning module {module_address} "
                        f"({module_index}/{module_total})"
                    )
                    # 0.16.1+ surfaces the vendor scan plan's
                    # multi-pass structure — include the pass position
                    # so users see "we're in pass 2/3, not stuck on
                    # one band" during the ~1 s pause between passes.
                    if pass_total > 1 and pass_index:
                        sub_label = f" sub={sub_byte}" if sub_byte else ""
                        base += f" — pass {pass_index}/{pass_total}{sub_label}"
                    if register_total:
                        base += (
                            f" — {registers_done}/{register_total} regs "
                            f"({decoded_records} records)"
                        )
                    else:
                        base += f" ({decoded_records} records)"
                    message = base
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
            # The library's ``register_total`` is now the cumulative
            # per-module target under the vendor plan (48 for output
            # modules + PC-Logic, 93 for PC-Link, 112 with broad_scan).
            # No HA-side capping needed — the library reports the
            # real targets accurately. Keep the 240 fallback for
            # forensic / legacy paths where register_total is 0.
            effective_register_total = register_total or 240
            self._update_discovery_state(
                phase=legacy_phase,
                message=message,
                current_module=module_address if module_address else None,
                modules_done=max(0, module_index - 1),
                modules_total=module_total,
                registers_done=registers_done,
                registers_total=effective_register_total,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Discovery progress handler failed: %s", err)

    # ------------------------------------------------------------------
    # Data update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> None:
        """Refresh latest data from the Nikobus system via polling.

        Total-blackout auto-recovery: if every poll in a single cycle
        fails (every output module times out), the bus is silent —
        most likely PC-Link / FTDI idle sleep after the 120 s gap
        between polls (issue #337). Trigger the same reconnect path
        the user used to manually invoke (close + reopen + handshake)
        so the integration self-heals.
        """
        if self.discovery_running:
            return None
        polled = 0
        failures = 0
        try:
            for module_type in MODULE_TYPES:
                if module_type in self.dict_module_data:
                    polled_n, failed_n = await self._refresh_module_type(
                        self.dict_module_data[module_type]
                    )
                    polled += polled_n
                    failures += failed_n
            return None
        except NikobusDataError as err:
            _LOGGER.error("Failed to fetch Nikobus data: %s", err)
            raise UpdateFailed(f"Data refresh failed: {err}") from err
        finally:
            if (
                polled > 0
                and failures == polled
                and not self._stopping
            ):
                _LOGGER.warning(
                    "Nikobus poll cycle: %d/%d commands timed out — "
                    "bus silent. Triggering reconnect (issue #337).",
                    failures,
                    polled,
                )
                # Background task — don't block the coordinator's
                # refresh-cycle slot. ``_handle_connection_lost`` is
                # idempotent (no-op if a reconnect task is already
                # running) so retry-storms are prevented.
                self.hass.async_create_background_task(
                    self._handle_connection_lost(),
                    name="nikobus_blackout_recovery",
                )

    async def _refresh_module_type(
        self, modules_dict: dict[str, Any]
    ) -> tuple[int, int]:
        """Poll each module, return (polled, failed) counts.

        Per-module / per-group timeouts are still swallowed (logged
        as ERROR) so a single transient failure doesn't block other
        modules from refreshing in the same cycle. The aggregate
        counts roll up to ``_async_update_data`` for blackout
        detection.
        """
        polled = 0
        failed = 0
        for address, module_data in modules_dict.items():
            normalized = str(address).upper()
            channels = module_data.get("channels", [])
            chan_count = len(channels)
            groups = (1,) if chan_count <= 6 else (1, 2)

            changed = False
            for g in groups:
                polled += 1
                try:
                    state_hex = await self.nikobus_command.get_output_state(normalized, g) or ""
                    if state_hex and len(state_hex) >= 12:
                        start = 0 if g == 1 else 6
                        buf = self._module_states.get(normalized)
                        if buf is None:
                            buf = bytearray(12)
                            self._module_states[normalized] = buf
                        new_bytes = bytes.fromhex(state_hex[:12])
                        if buf[start : start + 6] != new_bytes:
                            buf[start : start + 6] = new_bytes
                            changed = True
                    else:
                        failed += 1
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    failed += 1
                    # Per-group failures are noise on installs that
                    # hit periodic bus-silent windows (issue #337) —
                    # they show up as N separate ERROR entries in
                    # HA's System Log panel even though the aggregate
                    # WARNING "Nikobus poll cycle: N/N commands timed
                    # out — bus silent. Triggering reconnect." in
                    # ``_async_update_data`` already conveys the
                    # actionable signal. Keep individual failures at
                    # DEBUG for log-trace diagnostics; the WARNING +
                    # the subsequent "scheduling reconnect" line are
                    # what the user needs to see.
                    _LOGGER.debug(
                        "Error refreshing %s group %d: %s", normalized, g, err
                    )

            # Only wake this module's entities when its state actually
            # changed. The coordinator's own post-poll ``async_update_
            # listeners`` still re-renders everything (cheaply, since
            # entities diff before writing), so an unchanged module needs
            # no targeted dispatch — which on a quiet bus is every module.
            if changed:
                await self.async_event_handler(
                    "nikobus_refreshed",
                    {"impacted_module_address": normalized},
                )
        return polled, failed

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
        """Return 6-byte group state.

        Callers expect a fixed-length 6-byte response. The library's
        ``get_bytearray_group_state`` returns the raw slice, which
        comes back empty for a group whose end position exceeds the
        module's buffer length (e.g. group 2 on a 6-output module).
        Pad to six zero bytes in that case so the caller always sees
        the same shape regardless of module size.
        """
        if self.nikobus_command:
            result = self.nikobus_command.get_bytearray_group_state(
                address, int(group)
            )
            if len(result) < 6:
                return bytearray(6)
            return result
        return bytearray(6)

    @callback
    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Update a single channel in the state buffer."""
        if self.nikobus_command:
            self.nikobus_command.set_bytearray_state(address, channel, value)

    def set_bytearray_group_state(
        self, address: str, group: int | str, value: str
    ) -> None:
        """Update a group in the state buffer from a hex string.

        ``group`` accepts an int (1/2) or the string forms ("1"/"2") the
        actuator routes by; it is normalised with ``int(group)`` below.

        Out-of-range group writes are silently ignored. Without this
        guard, a slice assignment like ``buf[6:12] = ...`` against a
        6-byte buffer would extend it to 12 bytes — silently
        promoting a 6-output module to 12 outputs in the state
        store, which downstream consumers (channel iteration,
        diagnostics) interpret as real channels.
        """
        addr_upper = address.upper()
        buf = self._module_states.get(addr_upper)
        if buf is None:
            return
        start = 0 if int(group) == 1 else 6
        if start + 6 > len(buf):
            return
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

    @property
    def has_known_output_modules(self) -> bool:
        """True if at least one output-capable module is known.

        Gates the full module scan: without any known output modules the
        scan has nothing to walk. A PC Link inventory (or a migration
        from the legacy config file) must run first to populate storage.
        """
        return any(
            isinstance(mods, dict) and mods
            for m_type, mods in self.dict_module_data.items()
            if m_type in MODULE_TYPES
        )

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
            "status": phys.get("status"),
        }

    def get_button_context(
        self, bus_address: str
    ) -> tuple[str, str, dict[str, Any], dict[str, Any] | None] | None:
        """Return ``(physical_addr, key_label, op_point, phys)`` for the
        button op-point at ``bus_address``, or ``None``. Lets callers
        build the button's display name without re-walking the store."""
        hit = find_operation_point(self.dict_button_data, bus_address)
        if hit is None:
            return None
        physical_addr, key_label, op_point = hit
        phys = (self.dict_button_data or {}).get("nikobus_button", {}).get(physical_addr)
        return physical_addr, key_label, op_point, (phys if isinstance(phys, dict) else None)

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
                    "module": self.address_label(module_address),
                    "module_address": module_address,
                    "channel": out.get("channel"),
                    "mode": out.get("mode"),
                    "t1": out.get("t1"),
                    "t2": out.get("t2"),
                })
        return flattened

    def address_label(self, address: Any) -> str:
        """Return ``"Friendly Name (ADDRESS)"`` for a module / button bus
        address, from the HA device registry (so user renames are
        reflected), falling back to the bare uppercase address. Lets
        attributes show a human name while keeping the address for
        reference."""
        if not address:
            return ""
        addr = str(address).upper()
        if self.hass is not None:
            device = dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, addr)}
            )
            if device is not None:
                name = device.name_by_user or device.name
                if name and name.upper() != addr and addr not in name.upper():
                    return f"{name} ({addr})"
        return addr

    def get_scene_for_address(self, bus_address: Any) -> dict[str, Any] | None:
        """Return the classified CF/scene record an address triggers, or
        ``None`` — used to cross-reference a button with the scene it fires.

        A scene can have several trigger addresses (one Central Function,
        many inputs). The store is keyed on the canonical address, so we
        match the canonical key first and then any address listed in a
        scene's ``triggered_by``."""
        if self.cf_storage is None or not bus_address:
            return None
        addr = str(bus_address).upper()
        scenes = self.cf_storage.data.get("nikobus_cf", {})
        cf = scenes.get(addr)
        if isinstance(cf, dict):
            return cf
        for cf in scenes.values():
            if isinstance(cf, dict) and addr in (cf.get("triggered_by") or []):
                return cf
        return None

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
    # Stale-inventory management
    # ------------------------------------------------------------------

    async def purge_inventory_addresses(self, addresses: list[str]) -> dict[str, list[str]]:
        """Remove the given addresses from the persisted module + button stores.

        Each address is tried as both a module-store key and a button-store
        key; whichever matches is removed. The library deliberately doesn't
        mutate storage in ``detect_stale_inventory`` — this is the
        HA-side companion that consumes the manifest after the user has
        confirmed which addresses to drop.

        Saves both stores when at least one address was removed and
        schedules a config-entry reload so platforms drop entities for
        the purged addresses and the routing cache is rebuilt. Returns a
        breakdown of what happened so the caller can report results.
        """
        normalised = [
            str(addr).strip().upper() for addr in addresses if str(addr).strip()
        ]
        modules = self.module_storage.data.setdefault("nikobus_module", {})
        buttons = self.dict_button_data.setdefault("nikobus_button", {})
        removed_modules: list[str] = []
        removed_buttons: list[str] = []
        not_found: list[str] = []

        for addr in normalised:
            hit = False
            if addr in modules:
                modules.pop(addr, None)
                removed_modules.append(addr)
                hit = True
            if addr in buttons:
                buttons.pop(addr, None)
                removed_buttons.append(addr)
                hit = True
            if not hit:
                not_found.append(addr)

        if removed_modules or removed_buttons:
            await self.module_storage.async_save()
            await self.button_storage.async_save()
            self._rebuild_dict_module_data()
            self._invalidate_routing_cache()
            # Reload so platforms drop entities for the purged addresses.
            # Schedule rather than await — matches ``_async_options_updated``.
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

        return {
            "removed_modules": removed_modules,
            "removed_buttons": removed_buttons,
            "not_found": not_found,
        }

    # ------------------------------------------------------------------
    # Event / dispatcher
    # ------------------------------------------------------------------

    async def async_send_button_press(self, address: str) -> None:
        """Put a simulated button press on the bus as a short, spaced burst.

        A real Nikobus button emits its telegram repeatedly for as long
        as it's held, and modules only act on a command seen at least
        twice (the bus protocol's noise/collision guard). A single ``#N``
        frame is therefore unreliable under bus contention — the symptom
        being presses that "sometimes" do nothing. We mirror the
        reference firmware ("2 to register, 3 to be sure") by repeating
        the frame ``CONF_PRESS_REPEAT`` times (default 3) with a small
        inter-frame gap, kept short enough to read as a tap, not a hold.

        Shared by every HA-originated press: input buttons, the input
        A/B latch switch, software-scene feedback LEDs, and CF /
        light-scene activation.
        """
        if not address or self.nikobus_command is None:
            # No command handler before connect / during teardown —
            # nothing to send rather than an AttributeError.
            return
        try:
            repeats = max(1, int(self._press_repeat))
        except (TypeError, ValueError):
            repeats = DEFAULT_PRESS_REPEAT
        command = f"#N{address}\r#E1"
        for i in range(repeats):
            await self.nikobus_command.queue_command(command)
            if i < repeats - 1:
                await asyncio.sleep(PRESS_REPEAT_DELAY)

    async def async_event_handler(self, event: str, data: dict[str, Any]) -> None:
        """Send an HA-originated press, or wake the impacted module's entities.

        ``ha_button_pressed`` only queues the bus frame; the resulting bus
        feedback dispatches the targeted update once the state actually
        changes, so there's nothing to refresh here — returning avoids a
        pointless wake of every entity.
        """
        if event == "ha_button_pressed":
            await self.async_send_button_press(str(data.get("address") or ""))
            return

        if address := data.get("impacted_module_address"):
            async_dispatcher_send(self.hass, f"{DOMAIN}_update_{address}")

    # ------------------------------------------------------------------
    # Connection lost / reconnect
    # ------------------------------------------------------------------

    async def _handle_connection_lost(self) -> None:
        """Called by the listener when the connection drops, or by the
        blackout-recovery path in ``_async_update_data``.

        Coalesces concurrent calls. The current dedup check at the
        bottom (``_reconnect_task is None or done``) only prevents
        double-task-creation; it does NOT prevent ``nikobus_command.
        stop()`` from running twice mid-reconnect. That second stop
        races with the in-flight reconnect's ``connect()`` →
        ``_handshake()``, producing the visible
        ``Reconnect 1 failed: Cannot send: Not connected.`` error
        users see in HA's notification UI (issue #337).

        The race surfaces specifically when blackout-detection in
        ``_async_update_data`` triggers ``_handle_connection_lost``
        (call #1) → reconnect's fresh ``connect()`` opens a new FD →
        old listener's pending ``read()`` fails with
        ``IncompleteReadError`` → listener fires
        ``on_connection_lost`` → ``_handle_connection_lost`` (call #2)
        → second ``command.stop()`` corrupts the handshake. Coalescing
        at function entry collapses #2 into a no-op.
        """
        if self._stopping:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            _LOGGER.debug(
                "Reconnect already in progress — coalescing duplicate "
                "connection-lost notification"
            )
            return
        _LOGGER.warning("Nikobus connection lost — scheduling reconnect")
        self.async_update_listeners()
        if self.nikobus_command:
            await self.nikobus_command.stop()
        if self.nikobus_listener:
            # Stop the listener BEFORE the reconnect runs. If we leave
            # it running, its pending ``read()`` on the old reader
            # will fire when ``connect()`` opens a new FD — the kernel
            # closes the supplanted reader and the read raises
            # ``IncompleteReadError``. The library's ``connection.read()``
            # catches that and calls ``self.disconnect()``, which sets
            # ``_is_connected = False`` on the SHARED connection object
            # mid-handshake. The handshake's next ``send()`` then
            # raises ``Cannot send: Not connected.`` and the reconnect
            # attempt fails (issue #337 follow-up to PR #341).
            #
            # In the listener-initiated path (real read error), the
            # listener has already exited via its own ``break`` and
            # ``stop()`` is a no-op. Safe in both paths.
            await self.nikobus_listener.stop()
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
            _LOGGER.info("Nikobus reconnect attempt %d (delay %ds)", attempt, delay)
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
            except Exception:
                _LOGGER.exception("Subsystem restart failed after reconnect — retrying")
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

        # Cancel the actuator's in-flight press / refresh tasks too, so a
        # button press being processed during unload can't touch the
        # command handler / connection after we stop them below.
        actuator = getattr(self, "nikobus_actuator", None)
        if actuator:
            actuator.stop()

        # 2. Then stop subsystems in reverse start order.
        if self.nikobus_listener:
            try:
                await self.nikobus_listener.stop()
            except NikobusError as err:
                _LOGGER.error("Failed to stop listener: %s", err)
        if self.nikobus_command:
            try:
                await self.nikobus_command.stop()
            except NikobusError as err:
                _LOGGER.error("Failed to stop command handler: %s", err)
        try:
            await self.nikobus_connection.disconnect()
        except NikobusError as err:
            _LOGGER.error("Failed to disconnect: %s", err)

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def get_known_entity_unique_ids(self) -> set[str]:
        """Return the set of valid unique_ids for all Nikobus entities."""
        from .router import (
            build_routing,
            build_unique_id,
            input_latch_switch_unique_id,
            iter_input_module_children,
            iter_operation_points,
        )
        known: set[str] = set()
        routing = build_routing(self.dict_module_data)
        for specs in routing.values():
            for spec in specs:
                known.add(build_unique_id(spec.domain, spec.kind, spec.address, spec.channel))
        buttons = self.dict_button_data.get("nikobus_button", {})
        # Button + push-button ids, via the shared op-point enumerator
        # (same guard ladder the button/binary-sensor platforms use).
        for _addr, _key, op_point, _phys in iter_operation_points(buttons):
            bus_addr = op_point["bus_address"]
            known.add(f"{DOMAIN}_button_{bus_addr}")
            known.add(f"{DOMAIN}_push_button_{bus_addr}")
        # Stateful A/B latch switch ids for PC-Logic / Modular-Interface
        # inputs — same enumerator the switch platform creates from.
        for in_addr, _phys in iter_input_module_children(buttons):
            known.add(input_latch_switch_unique_id(in_addr))
        for scene in self.dict_scene_data.get("scene", []):
            if sid := scene.get("id"):
                known.add(f"{DOMAIN}_scene_{sid}")
        # CF / light-scene entities classified by the library during
        # discovery and surfaced by the scene platform as
        # ``NikobusCFSceneEntity`` (unique_id ``nikobus_cf_<addr>``).
        # Without these, ``_async_cleanup_orphan_entities`` evicts them
        # immediately after the scene platform creates them.
        if self.cf_storage is not None:
            for cf_addr in self.cf_storage.data.get("nikobus_cf", {}):
                known.add(f"nikobus_cf_{str(cf_addr).lower()}")
        known.add(f"{DOMAIN}_connection_status")
        known.add(f"{DOMAIN}_discovery_status")
        known.add(f"{DOMAIN}_discovery_progress")
        known.add(f"{DOMAIN}_pc_link_inventory_button")
        known.add(f"{DOMAIN}_module_scan_button")
        known.add(f"{DOMAIN}_import_nkb_names_button")
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

    async def _warn_if_legacy_config_files_present(self) -> None:
        """Warn if the deprecated manual-config files are still on disk.

        As of 3.0.0 these files are no longer imported for entity names;
        they're only consulted as the no-PC-Link inventory fallback.
        """
        present = await legacy_config_files_present(self.hass)
        if present:
            _LOGGER.warning(
                "Legacy Nikobus config file(s) found: %s. As of 3.0.0 these "
                "are no longer imported for entity names — names are managed "
                "in Home Assistant and preserved across reloads. They are "
                "only consulted as the inventory fallback for installs "
                "without a PC-Link; if you use a PC-Link/bridge you can "
                "delete them.",
                ", ".join(present),
            )

    def _invalidate_routing_cache(self) -> None:
        """Drop the cached router spec so the next access rebuilds it
        (e.g. after modules are discovered, purged, or edited)."""
        domain_data = self.hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(self.config_entry.entry_id, {})
        entry_data.pop("routing", None)

    async def async_on_module_save(self) -> None:
        """Persist the Store after discovery/user edits, then refresh derived views.

        Public hook: called by the library (registered as ``on_module_save``)
        and by the options flow after a module/channel edit.
        """
        await self.module_storage.async_save()
        self._rebuild_dict_module_data()
        # Clear the cached router spec so newly-discovered modules show up.
        self._invalidate_routing_cache()

    @staticmethod
    def _collect_button_linked_modules(phys: dict[str, Any]) -> set[str]:
        """Union of every module address referenced by any of a button's op-points."""
        linked: set[str] = set()
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, dict):
            return linked
        for op_point in op_points.values():
            if not isinstance(op_point, dict):
                continue
            for link in op_point.get("linked_modules") or []:
                if not isinstance(link, dict):
                    continue
                addr = link.get("module_address")
                if addr:
                    linked.add(str(addr).upper())
        return linked

    @staticmethod
    def _collect_button_outputs(phys: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten every output record under every op-point of a button.

        Returned dicts are the decoder's per-output records (channel,
        mode, payload, button_address, plus nikobus-connect 0.5.22+'s
        ``record_source``). Used by the registry-only residue check.
        """
        outputs: list[dict[str, Any]] = []
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, dict):
            return outputs
        for op_point in op_points.values():
            if not isinstance(op_point, dict):
                continue
            for link in op_point.get("linked_modules") or []:
                if not isinstance(link, dict):
                    continue
                for out in link.get("outputs") or []:
                    if isinstance(out, dict):
                        outputs.append(out)
        return outputs

    @staticmethod
    def _all_outputs_registry_sourced(outputs: list[dict[str, Any]]) -> bool:
        """True iff every output has ``record_source`` in the registry set.

        Returns False if ``outputs`` is empty, or if any output is
        missing the field. Pre-0.5.22 records (no ``record_source``)
        are treated as source-unknown and fall through to the existing
        classifier — backward compat without data migration.
        """
        if not outputs:
            return False
        return all(
            out.get("record_source") in _REGISTRY_SOURCES
            for out in outputs
        )

    def _has_pc_logic_module(self) -> bool:
        """True if the install has at least one PC-Logic module in the store.

        Gates the registry-only residue verdict: with PC-Logic absent,
        a button whose every output is registry-sourced is unambiguous
        residue (no real button-to-output link exists anywhere). With
        PC-Logic present, the same shape could be a legitimate
        PC-Logic-only scene trigger — fall through to the existing
        classifier and let the user adjudicate.
        """
        modules = self.module_storage.data.get("nikobus_module", {})
        if not isinstance(modules, dict):
            return False
        return any(
            isinstance(m, dict) and m.get("module_type") == "pc_logic"
            for m in modules.values()
        )

    def _surface_legacy_undecoded_buttons(
        self, buttons: dict[str, Any]
    ) -> None:
        """Create or clear the ``legacy_undecoded_buttons`` Repairs issue.

        Called from ``_reconcile_post_discovery`` *only* after a Stage-2
        scan-all completes — that's when both legacy buckets are
        meaningful:

          * ``legacy_undecoded`` — no decoded links across any op-point.
            Either intentionally unwired (HA-trigger pattern) or
            residue from a previous owner. HA cannot tell those apart.
          * ``legacy_orphan`` — has decoded links but either (a) every
            link points to an evicted module, or (b) every output
            record is registry-sourced with no PC-Logic in the install
            (residue programming from a previous owner that the
            current owner's DIN-button re-pairing didn't clear).

        Both buckets warrant user review before purge. The Repairs
        flow renders the combined candidate list with a multi-select.

        Issue auto-clears when the candidate list becomes empty (next
        scan-all). Recoverable: purged buttons reappear on the next
        PC-Link inventory if they're still in the project.
        """
        legacy_addrs = sorted(
            str(addr).upper()
            for addr, phys in buttons.items()
            if isinstance(phys, dict)
            and phys.get("status") in ("legacy_undecoded", "legacy_orphan")
        )
        issue_id = (
            f"{ISSUE_LEGACY_UNDECODED_BUTTONS}_{self.config_entry.entry_id}"
        )
        if not legacy_addrs:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_LEGACY_UNDECODED_BUTTONS,
            translation_placeholders={"count": str(len(legacy_addrs))},
            data={
                "entry_id": self.config_entry.entry_id,
                "addresses": legacy_addrs,
            },
        )

    async def _ingest_cf_broadcasts(self) -> None:
        """Persist the library's classified CF broadcasts into our store.

        Library-side (nikobus-connect 0.20.0+):
        ``NikobusDiscovery._classify_cf_broadcasts_from_unmatched`` runs
        at end-of-scan, pulls the ``38 41 XX`` (switch-pair) and
        ``38 80 XX`` (roller-pair) addresses out of the unmatched
        accumulator, attaches their (module, channel, mode) members
        from the command mapping, and exposes a ``CFBroadcast`` dict
        on ``discovered_cf_broadcasts``.

        We mirror that dict into ``cf_storage`` as a plain JSON-safe
        shape: ``{"nikobus_cf": {bus_address: {pattern, outputs}}}``.
        Persisting means scene entities survive across HA restarts;
        the next discovery refreshes the data idempotently.
        """
        if self.nikobus_discovery is None:
            return
        broadcasts = getattr(
            self.nikobus_discovery, "discovered_cf_broadcasts", None
        )
        if not broadcasts:
            # Library didn't classify anything this scan — preserve
            # whatever was persisted last time. (Don't wipe; a re-scan
            # that hits an empty bus shouldn't lose the user's scenes.)
            return

        flat: dict[str, dict[str, Any]] = {}
        for addr, cf in broadcasts.items():
            outputs = [
                {
                    "module_address": str(m.module_address).upper(),
                    "channel": int(m.channel),
                    "mode": str(m.mode),
                    "t1": getattr(m, "t1", None),
                    "t2": getattr(m, "t2", None),
                }
                for m in getattr(cf, "outputs", [])
            ]
            bus_address = str(getattr(cf, "bus_address", addr)).upper()
            triggered_by = [
                str(t).upper()
                for t in (getattr(cf, "triggered_by", None) or [bus_address])
            ]
            flat[str(addr).upper()] = {
                "bus_address": bus_address,
                "pattern": str(getattr(cf, "pattern", "unknown")),
                "outputs": outputs,
                # Every address that fires this CF (one CF, many triggers).
                "triggered_by": triggered_by,
            }

        # Preserve any .nkb-sourced scenes (added by async_import_nkb_names);
        # discovery never produces them, so a re-scan must not wipe them.
        preserved = {
            addr: entry
            for addr, entry in (self.cf_storage.data.get("nikobus_cf") or {}).items()
            if isinstance(entry, dict) and entry.get("source") == "nkb"
        }
        self.cf_storage.data["nikobus_cf"] = {**preserved, **flat}
        await self.cf_storage.async_save()
        _LOGGER.info(
            "CF broadcasts persisted: %d discovered + %d nkb-sourced (%s)",
            len(flat),
            len(preserved),
            sorted({**preserved, **flat}.keys()),
        )

    async def async_activate_cf_broadcast(self, bus_address: str) -> None:
        """Send the bus frame that activates a classified CF.

        Two CF flavours share this path, both keyed on the bus address
        that triggers the member outputs:

        * PC-Logic broadcast CFs (``38 41 XX`` / ``38 80 XX``) — what
          PC-Logic emits when the CF fires.
        * Light-scene CFs (``pattern == "light_scene"``) — the address
          is the real wall-button / IR trigger the members link to
          (e.g. an IR channel like ``0D1C9E``).

        In both cases the output modules carry link records pointing to
        this address, so we send the same wall-button-simulation frame
        ``#N{addr}\\r#E1``. The modules fire in unison — single-frame
        atomic activation, no per-channel round-trip.
        """
        if not bus_address:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cf_no_address",
            )
        if not self.nikobus_command:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
            )
        addr = bus_address.upper()
        _LOGGER.info("Activating Nikobus CF broadcast: %s", addr)
        await self.async_send_button_press(addr)

    async def _reconcile_post_discovery(
        self,
        discovered_devices: dict[str, Any] | None = None,
        inventory_query_type: InventoryQueryType | None = None,
    ) -> None:
        """Probe every output module + tag buttons by reachability.

        Runs after the library finalizes its inventory merge. The
        library merges *additively* (it adds discovered records but
        never evicts), so without this step a previous owner's
        residue (a second-hand PC-Link, a re-keyed install, etc.) would
        persist forever in the local stores.

        **Sweep state** arrives as kwargs from ``on_discovery_finished``
        (nikobus-connect 0.5.20+). Earlier versions cleared
        ``discovered_devices`` before firing the callback, so this
        integration used to snapshot it in ``on_module_save`` — see PR
        #331 for the history. 0.5.20 made the callback pass the state
        through directly, so neither workaround is needed.

        **Eviction predicate.** ``evict = manifest["absent_modules"]``.
        nikobus-connect 0.5.20 internally retries each probe with
        ``outer_attempts`` × ``outer_delay`` (PR #55 fixed the
        attempts-vs-wire-sends accounting bug and the queue dedup-vs-
        retry race), so a module appearing in ``absent_modules`` means
        it genuinely failed to ACK on multiple outer passes. No HA-
        side retry loop or combined-with-sweep predicate is needed.

        Eviction only fires when the user just performed a full PC-Link
        sweep (``inventory_query_type == InventoryQueryType.PC_LINK``);
        per-module scans never trigger it.

        Buttons are tagged into one of three buckets:

          * ``active`` — at least one ``linked_modules`` entry resolves
            to a surviving module.
          * ``legacy_orphan`` — every linked module was evicted.
          * ``legacy_undecoded`` — no ``linked_modules`` survived the
            scan (decoder may catch up later; flag surfaces it).

        Buttons stay in the store regardless of bucket — the ``status``
        field is the marker for HA UI / diagnostics.
        """
        if self.nikobus_discovery is None:
            return

        # --- Sweep snapshot --------------------------------------------
        # nikobus-connect 0.5.20 passes ``discovered_devices`` and
        # ``inventory_query_type`` through ``on_discovery_finished``;
        # we just filter to the modules-only subset.
        currently_swept: set[str] = set()
        if (
            inventory_query_type == InventoryQueryType.PC_LINK
            and isinstance(discovered_devices, dict)
        ):
            currently_swept = {
                str(addr).upper()
                for addr, dev in discovered_devices.items()
                if isinstance(dev, dict) and dev.get("category") == "Module"
            }

        # --- Probe with library-owned outer retry ----------------------
        self.discovery_sub_phase = DISCOVERY_SUB_PHASE_PROBING
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_PC_LINK,
            message="Probing modules for residue…",
            registers_done=self.discovery_registers_total,
        )
        try:
            manifest = await self.nikobus_discovery.detect_stale_inventory(
                outer_attempts=_PROBE_OUTER_ATTEMPTS,
                outer_delay=_PROBE_OUTER_DELAY_S,
            )
        except Exception:  # pragma: no cover - defensive
            _LOGGER.exception("Stale-inventory detection failed")
            return

        absent = {str(a).upper() for a in (manifest.get("absent_modules") or [])}
        present = {str(a).upper() for a in (manifest.get("present_modules") or [])}
        checked = manifest.get("checked") or []

        # --- Eviction --------------------------------------------------
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_PC_LINK,
            message="Reconciling discovered inventory…",
        )
        modules = self.module_storage.data.setdefault("nikobus_module", {})
        evicted: list[str] = []
        if currently_swept:
            for addr in list(modules.keys()):
                if str(addr).upper() in absent:
                    modules.pop(addr, None)
                    evicted.append(str(addr).upper())
        if evicted:
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_PC_LINK,
                message=f"Evicted {len(evicted)} stale module(s); finalizing…",
            )

        # --- Button bucketing ----------------------------------------
        remaining = {str(a).upper() for a in modules.keys()}
        bucket_counts = {
            "active": 0,
            "legacy_orphan": 0,
            "legacy_undecoded": 0,
            "synthesized_input": 0,
            "input_only": 0,
        }
        buttons = self.dict_button_data.setdefault("nikobus_button", {})
        # Topology gate for the registry-only residue check. With no
        # PC-Logic in the install, a button whose every output record
        # is sourced from PC-Link / PC-Logic registry memory has NO
        # output module recording the link — strong residue signal.
        # With PC-Logic present, the same shape could be a legitimate
        # scene trigger; defer to the existing classifier.
        has_pc_logic = self._has_pc_logic_module()
        for phys in buttons.values():
            if not isinstance(phys, dict):
                continue
            # Library-synthesized PC-Logic (05-201) and Modular Interface
            # (05-206) input children carry ``pc_logic_parent_address``
            # set by the synthesizer in nikobus-connect
            # ``_synthesize_pc_logic_inputs``. They model bus-event
            # sources that the parent module listens to internally, not
            # buttons that drive output modules — empty ``linked_modules``
            # is the steady state, not a residue signal. Bucket them on
            # their own status so the legacy-undecoded Repairs flow
            # leaves them alone.
            if phys.get("pc_logic_parent_address"):
                phys["status"] = "synthesized_input"
                bucket_counts["synthesized_input"] += 1
                continue
            # Universal Interface (Niko 05-058) in either mode is
            # input-only — its 4/8 contacts emit press telegrams but
            # don't write into output-module link tables (they're
            # designed to feed PC-Logic conditions). Same shape as a
            # synthesized PC-Logic input from the bucket-decision
            # perspective: empty ``linked_modules`` is the steady state.
            # Tag separately so the legacy-undecoded Repairs alert
            # doesn't false-positive on them.
            if phys.get("type") in INPUT_ONLY_BUTTON_TYPES:
                phys["status"] = "input_only"
                bucket_counts["input_only"] += 1
                continue
            linked = self._collect_button_linked_modules(phys)
            outputs = self._collect_button_outputs(phys)
            if not outputs:
                # No decoded links anywhere — pre-Stage-2 default OR
                # intentionally unwired button (HA-trigger pattern).
                status = "legacy_undecoded"
            elif (
                not has_pc_logic
                and self._all_outputs_registry_sourced(outputs)
            ):
                # nikobus-connect 0.5.22 residue filter: every output
                # comes from PC-Link / PC-Logic registry, and there's
                # no PC-Logic module to potentially justify a scene
                # trigger. Unambiguous residue programming from a
                # previous owner — surface for purge.
                status = "legacy_orphan"
            elif not (linked & remaining):
                # All decoded-target modules were evicted by the probe.
                status = "legacy_orphan"
            else:
                status = "active"
            phys["status"] = status
            bucket_counts[status] += 1

        # Surface the legacy-undecoded Repairs issue only after a
        # Stage-2 scan-all, when the verdict is meaningful (every
        # output module's register table was just read, so a button
        # still without ``linked_modules`` is genuinely either
        # intentionally unwired or residue — and HA can't distinguish
        # the two without user input).
        if (
            inventory_query_type != InventoryQueryType.PC_LINK
            and self._last_module_scan_was_full
        ):
            self._surface_legacy_undecoded_buttons(buttons)

        # Ingest the library's classified CF activation broadcasts (the
        # ``38 41 XX`` / ``38 80 XX`` addresses surfaced by
        # ``_classify_cf_broadcasts_from_unmatched``). Persists into the
        # CF store so scene entities survive HA restarts even when the
        # next discovery hasn't run yet.
        await self._ingest_cf_broadcasts()

        # Persist. Both stores save unconditionally so the ``status``
        # field on buttons + the eviction on modules land on disk.
        await self.module_storage.async_save()
        await self.button_storage.async_save()
        self._rebuild_dict_module_data()
        self._invalidate_routing_cache()
        self.invalidate_controlled_by_index()

        _LOGGER.info(
            "Post-discovery reconciliation: probed=%d present=%d absent=%d "
            "swept=%d evicted=%d | buttons active=%d legacy_orphan=%d "
            "legacy_undecoded=%d synthesized_input=%d",
            len(checked),
            len(present),
            len(absent),
            len(currently_swept),
            len(evicted),
            bucket_counts["active"],
            bucket_counts["legacy_orphan"],
            bucket_counts["legacy_undecoded"],
            bucket_counts["synthesized_input"],
        )
        if evicted:
            _LOGGER.info(
                "Evicted modules (absent_modules from probe): %s", evicted
            )

    async def _handle_discovery_finished(
        self,
        *,
        discovered_devices: dict[str, Any] | None = None,
        inventory_query_type: InventoryQueryType | None = None,
    ) -> None:
        """Signal discovery completion; optionally reload the config entry.

        Consumes nikobus-connect 0.5.20's kwargs-style callback (PR #55
        — passes sweep state through directly so consumers don't depend
        on instance-state lifecycle). Earlier library versions called
        this with no args; the defaults keep backward-compat for any
        old library shim, but the integration pins >= 0.5.20.
        """
        self.discovery_register_current = None
        # Don't unset ``discovery_running`` or set ``sub_phase = FINISHED``
        # here — the reconciliation step that follows still has several
        # seconds of bus probe + eviction work, and clearing the polling
        # guard (``_async_update_data`` early-returns when
        # ``discovery_running`` is True) lets the normal poll cycle
        # hammer the bus alongside the probe (issue #319 IKIKN 2.0.20
        # log — even ``1CEC`` regressed on the retry attempt). We land
        # on FINISHED + clear ``discovery_running`` only after the
        # reconciler returns.
        #
        # 2.11.3: bridge the reconciliation gap with FINALIZING so the
        # progress bar shows real movement (bar jumps to floor of
        # finalizing = 95%) during the multi-second reconcile/probe/save
        # window — without this, the bar would freeze at whatever phase
        # was last live (30% after a PC-Link inventory-only scan that
        # skips register-scan; 95% after a Scan-All) until the entire
        # reconciliation completed and we jumped to FINISHED.
        self.discovery_sub_phase = DISCOVERY_SUB_PHASE_FINALIZING
        self._update_discovery_state(
            message=(
                f"Merging {self.discovery_decoded_records} discovered records…"
                if self.discovery_decoded_records
                else "Finalising discovery…"
            ),
        )
        await self._reconcile_post_discovery(
            discovered_devices, inventory_query_type
        )

        self.discovery_running = False
        self.discovery_sub_phase = DISCOVERY_SUB_PHASE_FINISHED
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
            except Exception:
                _LOGGER.exception("Failed to reload config entry after discovery")

        self._reload_task = self.hass.async_create_task(_reload())

    # ------------------------------------------------------------------
    # Discovery progress API (called by options flow / buttons / sensors)
    # ------------------------------------------------------------------

    @property
    def discovery_progress_percent(self) -> float:
        """Overall progress estimate (0-100) across all discovery sub-phases.

        Phases are stacked by weight (see const.DISCOVERY_WEIGHT_*):
        inventory → identity → register_scan → finalizing. Within
        register_scan, progress is (completed_modules + partial_current) /
        total_modules. Returned as a float with ~0.1 resolution so the UI
        can show sub-percent movement — each of the 240 register ticks in
        a module only advances the bar by a fraction of a percent, so an
        integer-rounded value looks frozen for tens of seconds at a time.
        """
        if self.discovery_sub_phase in (DISCOVERY_SUB_PHASE_IDLE, DISCOVERY_SUB_PHASE_ERROR):
            return 0.0
        if self.discovery_sub_phase == DISCOVERY_SUB_PHASE_FINISHED:
            return 100.0

        # Cumulative floor — everything before the current phase is "done".
        floor = 0
        if self.discovery_sub_phase == DISCOVERY_SUB_PHASE_INVENTORY:
            floor = 0
            phase_weight = DISCOVERY_WEIGHT_INVENTORY
            # 2.11.2: ``parse_inventory_response`` counts each PC-Link
            # inventory frame into ``discovery_registers_done``, so the
            # bar can track real progress rather than parking at the
            # midpoint of the inventory weight. Fall back to 0.5 only
            # when the total isn't set yet (transition window).
            if self.discovery_registers_total:
                phase_frac = min(
                    1.0,
                    self.discovery_registers_done / self.discovery_registers_total,
                )
            else:
                phase_frac = 0.5
        elif self.discovery_sub_phase == DISCOVERY_SUB_PHASE_IDENTITY:
            floor = DISCOVERY_WEIGHT_INVENTORY
            phase_weight = DISCOVERY_WEIGHT_IDENTITY
            total = self.discovery_modules_total or 1
            done = self.discovery_modules_done
            # 2.11.2: include per-module register progress so the bar
            # moves smoothly during the ~30 s the library takes to scan
            # one module's 96 identity registers, instead of jumping
            # per-module (~0.4 % per step on a 47-module install).
            per_module = 0.0
            if self.discovery_registers_total:
                per_module = min(
                    1.0,
                    self.discovery_registers_done / self.discovery_registers_total,
                )
            phase_frac = min(1.0, (done + per_module) / total)
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
                return 10.0
            if self.discovery_phase == DISCOVERY_PHASE_MODULE_SCAN:
                return 40.0
            return 0.0

        raw = floor + phase_frac * phase_weight

        # Each discovery button runs only one slice of the full pipeline.
        # Rescale that slice to span the whole bar so a standalone scan
        # reads 0→100 instead of, e.g., Load Existing Installation opening
        # at 30 % — the cumulative weight of the inventory+identity phases
        # it skips. ``"full"`` (a combined run) keeps the raw stacked value.
        overview_span = DISCOVERY_WEIGHT_INVENTORY + DISCOVERY_WEIGHT_IDENTITY
        if self._discovery_scope == "module_scan":
            raw = (raw - overview_span) / (100 - overview_span) * 100
        elif self._discovery_scope == "inventory":
            raw = raw / overview_span * 100

        return min(99.9, round(max(0.0, raw), 1))

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

    # Timeout for the PC-Link ``#A`` *first-response* probe. PC-Link
    # normally answers in well under 1 s; 8 s leaves headroom for slow
    # buses. This is NOT the inventory-completion timeout — the library
    # owns that (its own 10 s inactivity timer fires
    # ``on_discovery_finished``). Once we see any PC-Link response we
    # commit to PC-Link and wait for the full inventory without a
    # coordinator-side timeout.
    _PCLINK_PROBE_TIMEOUT = 8.0
    # After a first-response probe times out we still wait briefly for
    # the library's inactivity timer (default 10 s) to fire
    # ``on_discovery_finished`` so state resets cleanly before the
    # manual-files fallback. Tests override this to keep run-time short.
    _PCLINK_FINALIZE_WAIT_AFTER_TIMEOUT = 15.0

    async def start_pc_link_inventory(self, *, auto_reload: bool = True) -> None:
        """Step 1 of discovery — unified inventory source + friendly-name overlay.

        Logic, in order:

        1. **Probe PC-Link** with the ``#A`` broadcast (timeout
           ``_PCLINK_PROBE_TIMEOUT``). If PC-Link responds, its result
           populates ``dict_module_data`` (and the button registry from
           per-address identity reads). Existing behaviour.

        2. **Fall back to manual files** when the probe times out — no
           PC-Link on the bus. The manual config files
           (``nikobus_module_config.json`` / ``nikobus_button_config.json``)
           become the inventory source. If neither file exists, raise
           ``no_inventory_source`` — we can't proceed.

        3. **Overlay friendly names** from the manual files onto the
           live stores REGARDLESS of which source step 1 used. PC-Link
           installs benefit too: PC-Link gives addresses and channel
           counts but generic names; the file's descriptions /
           ``entity_type`` / roller times override those.

        Step 2 (per-module register scan for link records) is a
        separate user action — ``start_module_scan``. It picks up
        whatever ``dict_module_data`` step 1 left in place.
        """
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
        self._discovery_auto_reload = auto_reload
        self._discovery_finished_event.clear()
        self._last_module_scan_was_full = False
        self._discovery_scope = "inventory"
        self._update_discovery_state(
            phase=DISCOVERY_PHASE_PC_LINK,
            message="Probing for PC-Link…",
            current_module=None,
            modules_done=0,
            modules_total=0,
            registers_done=0,
            registers_total=92,
            error=None,
        )

        # 1. PC-Link probe with timeout.
        used_pclink = await self._try_pclink_inventory()

        # 2. Fall back to manual files if probe timed out / failed.
        if not used_pclink:
            applied = await self._apply_manual_inventory_as_fallback()
            if not applied:
                self._update_discovery_state(
                    phase=DISCOVERY_PHASE_ERROR,
                    message=(
                        "No PC-Link detected and no manual config files "
                        "found. Install a PC-Link or create "
                        "nikobus_module_config.json in your config dir."
                    ),
                    error="no_inventory_source",
                )
                self.discovery_running = False
                self._discovery_finished_event.set()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_inventory_source",
                )

    async def _try_pclink_inventory(self) -> bool:
        """Kick off the ``#A`` broadcast and decide if PC-Link is present.

        Two-phase wait:

        1. **First-response probe** — wait up to
           ``_PCLINK_PROBE_TIMEOUT`` for the *first* inventory frame
           (``_pclink_first_response_event``, set in
           ``_discovery_frame_callback``). Any response proves PC-Link is
           alive on the bus.
        2. **Full completion** — once PC-Link is confirmed, wait
           ``_discovery_finished_event`` with NO coordinator timeout
           (real inventories on big installs take 30–60 s; the library
           owns inventory-completion timing via its own 10 s inactivity
           timer).

        Returns True if PC-Link responded, False if the first-response
        probe times out (caller falls back to manual files). On False
        we also wait briefly for the library's own ``on_discovery_finished``
        so the discovery state machine resets cleanly before fallback.
        """
        self._pclink_first_response_event.clear()
        try:
            await self.nikobus_discovery.start_inventory_discovery()
            try:
                await asyncio.wait_for(
                    self._pclink_first_response_event.wait(),
                    timeout=self._PCLINK_PROBE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                _LOGGER.info(
                    "No PC-Link response within %.1fs — falling back to "
                    "manual config files",
                    self._PCLINK_PROBE_TIMEOUT,
                )
                # Library's inactivity timer will fire on_discovery_finished
                # shortly; wait for it so state resets before fallback.
                try:
                    await asyncio.wait_for(
                        self._discovery_finished_event.wait(),
                        timeout=self._PCLINK_FINALIZE_WAIT_AFTER_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Step 1: library did not finalise inventory after "
                        "probe timeout; proceeding with fallback anyway."
                    )
                return False

            # PC-Link answered — let the full inventory complete.
            await self._discovery_finished_event.wait()
            return True
        except asyncio.CancelledError:
            self.discovery_running = False
            self._discovery_finished_event.set()
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Step 1: PC-Link probe failed (%s) — falling back to "
                "manual config files.",
                err,
            )
            return False

    async def _apply_manual_inventory_as_fallback(self) -> bool:
        """Import ``nikobus_module_config.json`` + ``nikobus_button_config.json``
        as the inventory source. Returns True if at least one file was
        loaded; the caller fails the discovery if both are absent.
        """
        from .nkbmanual import async_apply_manual_config
        try:
            changed = await async_apply_manual_config(
                self.hass, self.module_storage, self.dict_button_data
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Manual inventory import failed")
            return False
        if changed:
            await self.module_storage.async_save()
            await self.button_storage.async_save()
            self._rebuild_dict_module_data()
        return changed

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

        # Tracked for ``_reconcile_post_discovery`` — only after a
        # scan-all is ``legacy_undecoded`` a trustworthy signal.
        self._last_module_scan_was_full = module_address is None
        self._discovery_scope = "module_scan"

        if module_address:
            target = module_address.strip().upper()
            total = 1
            message = f"Scanning module {target}…"
            self._discovery_module_order = [target]
        else:
            target = "ALL"
            # Build the list of addresses we expect the library to walk
            # during the "ALL" scan, mirroring the library's own
            # queue-builder filter (nikobus_connect/discovery/discovery.py
            # ~line 1115). The library excludes feedback_module /
            # other_module; we use the same filter here so the progress
            # total matches what the library actually scans.
            #
            # ``pc_logic`` is intentionally NOT excluded as of
            # nikobus-connect 0.4.11 — the library register-scans
            # 05-201 modules with the PcLogicDecoder so installs that
            # route button → output via PC-Logic can capture the
            # BP-cell data needed for the real decoder.
            #
            # ``pc_link`` is intentionally NOT excluded as of
            # nikobus-connect 0.5.0 — the library now register-scans
            # 05-200 modules too, with PcLinkDecoder emitting
            # structured "PC-Link module-registry record" and
            # "PC-Link link record" INFO log lines. Stage 2a is
            # visibility-only (decoder returns None — no merge yet),
            # but the queue alignment must match either way so the
            # progress total stays correct and the no-modules-known
            # gate below doesn't falsely reject scans on installs
            # that have only a PC-Link.
            self._discovery_module_order = []
            for m_type, modules in self.dict_module_data.items():
                if m_type in ("feedback_module", "other_module"):
                    continue
                if isinstance(modules, dict):
                    self._discovery_module_order.extend(
                        str(addr).upper() for addr in modules.keys()
                    )
            _LOGGER.debug(
                "Module scan (all) — buckets=%s, queue=%s",
                {k: list(v.keys()) if isinstance(v, dict) else v
                 for k, v in self.dict_module_data.items()},
                self._discovery_module_order,
            )
            total = len(self._discovery_module_order)
            if total == 0:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_modules_known",
                )
            message = f"Scanning {total} modules…"

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

    async def async_import_nkb_names(
        self,
        categories: set[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, int]:
        """Import names, rooms, channel names and scenes from a ``.nkb``.

        ``categories`` selects what to apply (default: everything):

        * ``"device_names"`` — module / button / IR-receiver **device**
          names (``Name (Room)``).
        * ``"channel_names"`` — the per-output **entity** names (the
          light / cover / switch the user actually toggles, e.g.
          ``Appliques Salon``, ``Terrasse``).
        * ``"areas"`` — each device into a Home Assistant Area = its room.
        * ``"scenes"`` — match / create the Central Function scene entities.

        ``overwrite`` forces a name/Area even where the user has set their
        own; default off (suggested, never clobbers a manual rename).

        Returns a per-category count summary.
        """
        from homeassistant.helpers import area_registry as ar

        from .nkbnames import find_nkb_file, parse_nkb

        cats = set(categories) if categories else set(NKB_IMPORT_CATEGORIES)

        path = find_nkb_file(self.hass.config.config_dir)
        if path is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="nkb_not_found"
            )
        try:
            data = await self.hass.async_add_executor_job(parse_nkb, path)
        except Exception as err:
            _LOGGER.exception("Failed to read .nkb file %s", path)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="nkb_parse_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        name_map = data.addresses  # {ADDR: (name, room)}

        # Scenes: match named groups to existing CFs (light scenes) and
        # create entities for the rest (shutter / master groups) — only if
        # the "scenes" category is selected.
        cf_name_by_addr: dict[str, str] = {}
        scenes_created = 0
        if "scenes" in cats and self.cf_storage is not None:
            scene_by_members = {sc.members: sc.name for sc in data.scenes}
            matched_scene_members: set[frozenset[tuple[str, int, str]]] = set()
            for cf_addr, cf in self.cf_storage.data.get("nikobus_cf", {}).items():
                hit = scene_by_members.get(_cf_member_set(cf))
                if hit:
                    cf_name_by_addr[str(cf_addr).upper()] = hit
                    matched_scene_members.add(_cf_member_set(cf))
            graph = self._build_routing_graph()
            existing = self.cf_storage.data.setdefault("nikobus_cf", {})
            new_entries: dict[str, dict[str, Any]] = {}
            for sc in data.scenes:
                if sc.members in matched_scene_members or not sc.members:
                    continue
                hit = graph.get(sc.members)
                if hit is None:
                    continue
                addrs, outputs = hit
                canonical = addrs[0]
                if canonical in existing or canonical in new_entries:
                    continue
                new_entries[canonical] = {
                    "bus_address": canonical,
                    "pattern": "nkb_scene",
                    "outputs": outputs,
                    "triggered_by": addrs,
                    "source": "nkb",
                    "name": sc.name,
                }
                cf_name_by_addr[canonical] = sc.name
            if new_entries:
                existing.update(new_entries)
                await self.cf_storage.async_save()
                scenes_created = len(new_entries)

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        entry_id = self.config_entry.entry_id

        def _lookup(device: Any) -> tuple[str, str] | None:
            for domain, ident in device.identifiers:
                if domain != DOMAIN:
                    continue
                key = ident.upper()
                if key in name_map:
                    return name_map[key]
                if key in cf_name_by_addr:
                    return (cf_name_by_addr[key], "")
            return None

        do_names = "device_names" in cats
        do_areas = "areas" in cats
        matched: dict[str, str] = {}  # device_id -> display name
        devices_named = areas_set = 0
        for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
            hit = _lookup(device)
            if hit is None:
                continue
            name, room = hit
            display = f"{name} ({room})" if room else name
            matched[device.id] = display
            if do_names:
                # Non-overwrite sets the integration default (``name``), so a
                # manual rename (``name_by_user``) still wins; overwrite sets
                # ``name_by_user`` to force the .nkb name.
                if overwrite:
                    if device.name_by_user != display:
                        dev_reg.async_update_device(device.id, name_by_user=display)
                        devices_named += 1
                elif device.name != display:
                    dev_reg.async_update_device(device.id, name=display)
                    devices_named += 1
            if do_areas and room and (overwrite or device.area_id is None):
                area = area_reg.async_get_area_by_name(
                    room
                ) or area_reg.async_create(room)
                if device.area_id != area.id:
                    dev_reg.async_update_device(device.id, area_id=area.id)
                    areas_set += 1

        entities = list(er.async_entries_for_config_entry(ent_reg, entry_id))

        # Device-name → the entity of a single-entity matched device (wall
        # buttons, latch switches); multi-entity modules inherit the device
        # name, and their channels are named individually below.
        entities_named = 0
        if do_names:
            by_device: dict[str, list[Any]] = {}
            for ent in entities:
                if ent.device_id:
                    by_device.setdefault(ent.device_id, []).append(ent)
            for device_id, display in matched.items():
                ents = by_device.get(device_id, [])
                if len(ents) == 1 and _apply_entity_name(
                    ent_reg, ents[0], display, overwrite
                ):
                    entities_named += 1

        # Channel names — the per-output light / cover / switch entities.
        channels_named = 0
        if "channel_names" in cats and data.outputs:
            for ent in entities:
                key = _output_entity_key(ent.unique_id)
                if key is None:
                    continue
                nm = data.outputs.get(key)
                if nm and _apply_entity_name(ent_reg, ent, nm, overwrite):
                    channels_named += 1

        _LOGGER.info(
            "Imported .nkb from %s (overwrite=%s, categories=%s): %d devices, "
            "%d device-entities, %d channels, %d areas, %d scenes named, "
            "%d scenes created",
            path.name,
            overwrite,
            sorted(cats),
            devices_named,
            entities_named,
            channels_named,
            areas_set,
            len(cf_name_by_addr) - scenes_created,
            scenes_created,
        )

        if scenes_created:
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

        return {
            "devices": devices_named,
            "entities": entities_named,
            "channels": channels_named,
            "areas": areas_set,
            "scenes": len(cf_name_by_addr) - scenes_created,
            "scenes_created": scenes_created,
        }

    def _build_routing_graph(
        self,
    ) -> dict[frozenset[tuple[str, int, str]], tuple[list[str], list[dict[str, Any]]]]:
        """Map every op-point's member set → ``(firing addresses, outputs)``.

        The routing graph is the full set of ``trigger → linked outputs``
        relations from the button store — the same data discovery decodes.
        Used to find the on-bus address that fires a named ``.nkb`` scene
        group (matched by member set), including the shutter / master
        groups that have no light-scene mode and so never become CF
        entities on their own. Addresses driving an identical output set
        (one scene, several triggers) are grouped; the sorted-first is the
        canonical activation address.
        """
        graph: dict[
            frozenset[tuple[str, int, str]], tuple[list[str], list[dict[str, Any]]]
        ] = {}
        buttons = (self.dict_button_data or {}).get("nikobus_button", {})
        if not isinstance(buttons, dict):
            return {}
        for phys in buttons.values():
            if not isinstance(phys, dict):
                continue
            for op in (phys.get("operation_points") or {}).values():
                if not isinstance(op, dict):
                    continue
                addr = op.get("bus_address")
                if not isinstance(addr, str) or not addr:
                    continue
                outputs: list[dict[str, Any]] = []
                seen: set[tuple[str, int, str]] = set()
                for link in op.get("linked_modules") or []:
                    if not isinstance(link, dict):
                        continue
                    mod = link.get("module_address")
                    if not isinstance(mod, str):
                        continue
                    for o in link.get("outputs") or []:
                        if not isinstance(o, dict):
                            continue
                        ch = o.get("channel")
                        mode = o.get("mode")
                        if not (isinstance(ch, int) and isinstance(mode, str)):
                            continue
                        dedupe = (mod.upper(), ch, mode)
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        outputs.append(
                            {
                                "module_address": mod.upper(),
                                "channel": ch,
                                "mode": mode,
                                "t1": o.get("t1") if isinstance(o.get("t1"), str) else None,
                                "t2": o.get("t2") if isinstance(o.get("t2"), str) else None,
                            }
                        )
                members = _member_set_from_outputs(outputs)
                if not members:
                    continue
                entry = graph.setdefault(members, ([], outputs))
                entry[0].append(addr.upper())
        return {m: (sorted(set(a)), o) for m, (a, o) in graph.items()}
