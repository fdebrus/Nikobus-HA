"""Discovery lifecycle for the Nikobus coordinator.

This is a mixin, mixed into :class:`NikobusDataCoordinator` as a base
class. It carries the full discovery subsystem — PC-Link inventory and
per-module register scans, phase/progress reporting, post-discovery
reconciliation (residue eviction + button bucketing), CF-broadcast
ingestion, and ``.nkb`` name import — which is cohesive enough to live
in its own file but shares the coordinator's state and HA lifecycle, so
it is not a standalone object.

``self`` is always a ``NikobusDataCoordinator`` at runtime; the
``TYPE_CHECKING`` block below declares the coordinator surface these
methods rely on so the file type-checks under ``strict`` on its own.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

from nikobus_connect.discovery import InventoryQueryType

from .const import (
    DISCOVERY_PHASE_ERROR,
    DISCOVERY_PHASE_FINISHED,
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
    ISSUE_CORRUPT_MODULES,
    ISSUE_LEGACY_UNDECODED_BUTTONS,
    NKB_IMPORT_CATEGORIES,
    SIGNAL_DISCOVERY_STATE,
)
from .nkbreconcile import (
    cf_member_set,
    classify_button_status,
    flatten_cf_broadcasts,
    has_pc_logic_module,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from nikobus_connect import NikobusCommandHandler
    from nikobus_connect.discovery import NikobusDiscovery

    from .coordinator import NikobusConfigEntry
    from .nkbstorage import (
        NikobusButtonStorage,
        NikobusCFStorage,
        NikobusModuleStorage,
    )

_LOGGER = logging.getLogger(__name__)

# Outer-probe parameters passed to nikobus-connect's
# ``detect_stale_inventory(outer_attempts=N, outer_delay=S)`` (issue #319).
_PROBE_OUTER_ATTEMPTS = 2
_PROBE_OUTER_DELAY_S = 3.0

#: The library's auto-generated per-key device name ("Push button 1A
#: #N9A43A2"). Only names matching this are rewritten by the .nkb
#: import's key pass — IR / PC-Logic input keys and user-set names
#: never match and are left alone.
_PUSH_BUTTON_NAME_RE = re.compile(r"^Push button (\S+) #N[0-9A-Fa-f]{6}$")


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


class NikobusDiscoveryMixin:
    """Discovery lifecycle, mixed into :class:`NikobusDataCoordinator`."""

    if TYPE_CHECKING:
        # --- Provided by NikobusDataCoordinator (the concrete class) ---
        hass: HomeAssistant
        config_entry: NikobusConfigEntry
        module_storage: NikobusModuleStorage
        button_storage: NikobusButtonStorage
        cf_storage: NikobusCFStorage
        nikobus_discovery: NikobusDiscovery | None
        nikobus_command: NikobusCommandHandler | None
        dict_module_data: dict[str, Any]
        dict_button_data: dict[str, Any]
        discovery_running: bool
        discovery_phase: str
        discovery_sub_phase: str
        discovery_status_message: str
        discovery_current_module: str | None
        discovery_modules_done: int
        discovery_modules_total: int
        discovery_registers_done: int
        discovery_registers_total: int
        discovery_register_current: int | None
        discovery_decoded_records: int
        discovery_last_error: str | None
        inventory_query_type: InventoryQueryType | None
        discovery_module: Any
        discovery_module_address: str | None
        _discovery_finished_event: asyncio.Event
        _pclink_first_response_event: asyncio.Event
        _discovery_auto_reload: bool
        _discovery_module_order: list[str]
        _discovery_scope: str
        _last_module_scan_was_full: bool
        _reload_task: asyncio.Task[None] | None

        def _rebuild_dict_module_data(self) -> None: ...
        def _invalidate_routing_cache(self) -> None: ...
        def invalidate_controlled_by_index(self) -> None: ...
        async def async_send_button_press(self, address: str) -> None: ...

    def channel_label_map(self) -> dict[tuple[str, int], str]:
        """Map ``(MODULE_ADDR_UPPER, channel) -> output channel name``.

        Reads the per-channel output entities from the registry, so the
        names reflect the ``.nkb`` channel-name import *and* any manual
        rename — e.g. ``("0E6C", 8) -> "Boudoir - Plafonnier"``. Lets CF
        scene attributes show human channel names instead of bare numbers.
        Channels with no name are omitted.
        """
        out: dict[tuple[str, int], str] = {}
        if self.hass is None:
            return out
        ent_reg = er.async_get(self.hass)
        for ent in er.async_entries_for_config_entry(
            ent_reg, self.config_entry.entry_id
        ):
            key = _output_entity_key(ent.unique_id)
            if key is None:
                continue
            name = ent.name or ent.original_name
            if name:
                out[key] = name
        return out

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

    def _surface_corrupt_modules(self) -> None:
        """Create or clear the ``corrupt_modules`` Repairs issue.

        The library (nikobus-connect 0.27.2+) flags any module whose
        link table doesn't align with the scanned register window —
        genuine flash corruption, the same thing the Nikobus PC software
        reports as "reprogram this module". Their link decode was
        skipped (no phantom buttons), so the only action is to tell the
        user to reprogram. The issue is informational (not HA-fixable);
        it auto-clears on the next module scan once the module reads
        cleanly again.
        """
        corrupt = sorted(
            str(a).upper()
            for a in getattr(self.nikobus_discovery, "corrupt_link_tables", None)
            or ()
        )
        issue_id = f"{ISSUE_CORRUPT_MODULES}_{self.config_entry.entry_id}"
        if not corrupt:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        _LOGGER.warning(
            "Discovery: %d module(s) have a corrupt link table and were "
            "skipped — reprogram them in the Nikobus PC software: %s",
            len(corrupt),
            corrupt,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_CORRUPT_MODULES,
            translation_placeholders={
                "count": str(len(corrupt)),
                "modules": ", ".join(corrupt),
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

        flat = flatten_cf_broadcasts(broadcasts)

        # Carry ``.nkb`` names across a re-scan. Discovery never produces
        # names — they are applied by the ``.nkb`` import afterward and
        # persisted onto the CF record. Freshly discovered broadcasts have
        # none, so overwriting storage with ``flat`` would wipe every name.
        # That matters now that an *unnamed* button-backed light-scene is no
        # longer surfaced (see ``is_surfaced_cf_scene``): losing the name
        # would silently drop the user's named scenes on a plain re-scan.
        # Re-apply each prior name to the matching fresh CF — by canonical
        # address first, then by member set (stable even if the canonical
        # trigger address shifts between scans).
        old = self.cf_storage.data.get("nikobus_cf") or {}
        name_by_addr: dict[str, str] = {}
        name_by_members: dict[frozenset[tuple[str, int, str]], str] = {}
        for prev_addr, prev in old.items():
            if not isinstance(prev, dict):
                continue
            nm = prev.get("name")
            if isinstance(nm, str) and nm.strip():
                name_by_addr[str(prev_addr).upper()] = nm.strip()
                name_by_members.setdefault(cf_member_set(prev), nm.strip())
        carried = 0
        for addr, cf in flat.items():
            if not isinstance(cf, dict):
                continue
            existing = cf.get("name")
            if isinstance(existing, str) and existing.strip():
                continue
            name = name_by_addr.get(str(addr).upper()) or name_by_members.get(
                cf_member_set(cf)
            )
            if name:
                cf["name"] = name
                carried += 1

        self.cf_storage.data["nikobus_cf"] = flat
        await self.cf_storage.async_save()
        _LOGGER.info(
            "CF broadcasts persisted: %d discovered, %d names carried over (%s)",
            len(flat),
            carried,
            sorted(flat.keys()),
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
        # NOTE: no ``phase=`` override here (or below). The coarse phase
        # must stay whatever the run was (module_scan for a register
        # scan) — forcing it back to pc_link made the status sensor's
        # state REGRESS at the end of every module scan, and dropped
        # the progress bar to the legacy 10% fallback for the 5-15 s
        # the probe takes.
        self.discovery_sub_phase = DISCOVERY_SUB_PHASE_PROBING
        self._update_discovery_state(
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
        # Topology gate for the registry-only residue check (see
        # ``classify_button_status``): with no PC-Logic in the install, a
        # button whose every output record is registry-sourced has no
        # output module recording the link — a strong residue signal.
        has_pc_logic = has_pc_logic_module(self.module_storage.data)
        for phys in buttons.values():
            if not isinstance(phys, dict):
                continue
            status = classify_button_status(phys, remaining, has_pc_logic)
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

        # Surface modules the library flagged as having a corrupt link
        # table (decode skipped — see nikobus-connect 0.27.2). Only when
        # a register scan actually ran (a module scan, not a pure
        # PC-Link inventory), so a later inventory-only run doesn't clear
        # a still-valid corruption warning.
        if inventory_query_type != InventoryQueryType.PC_LINK:
            self._surface_corrupt_modules()

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
            if self.discovery_identity_expected:
                # Response-driven: the library queues all N×96 identity
                # reads up front and emits progress per QUEUED command
                # (racing to ~100 % in <1 s while the bus scan takes
                # ~25 s). The frame callback counts the $2E answers as
                # they actually arrive — that's the real progress.
                phase_frac = min(
                    1.0,
                    self.discovery_identity_responses
                    / self.discovery_identity_expected,
                )
            else:
                # Fallback for older libraries that don't surface the
                # per-address total: the old module-index estimate.
                total = self.discovery_modules_total or 1
                done = self.discovery_modules_done
                per_module = 0.0
                if self.discovery_registers_total:
                    per_module = min(
                        1.0,
                        self.discovery_registers_done
                        / self.discovery_registers_total,
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
        elif self.discovery_sub_phase == DISCOVERY_SUB_PHASE_PROBING:
            # Post-discovery residue probe + eviction (reconciliation).
            # Sits AFTER finalizing's midpoint: without this branch the
            # property fell through to the legacy fallback and the bar
            # DROPPED from ~97% to 10% for the 5-15 s the probe takes,
            # then jumped to 100 — the single most visible progress
            # glitch, present at the end of every discovery run.
            floor = (
                DISCOVERY_WEIGHT_INVENTORY
                + DISCOVERY_WEIGHT_IDENTITY
                + DISCOVERY_WEIGHT_REGISTER_SCAN
            )
            phase_weight = DISCOVERY_WEIGHT_FINALIZING
            phase_frac = 0.75
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
        # Claim the running flag NOW, not when the library flips it a
        # beat later inside ``start_inventory_discovery`` — that gap is
        # a double-press race (two UI presses both passing the check
        # above), and the flag drives the bridge buttons' availability
        # so the UI should grey out on the same dispatch that announces
        # the probe. Every exit path below (probe error/cancel,
        # fallback failure, library finish callback) already resets it.
        self.discovery_running = True
        self._discovery_auto_reload = auto_reload
        self._discovery_finished_event.clear()
        self._last_module_scan_was_full = False
        self._discovery_scope = "inventory"
        self.discovery_identity_responses = 0
        self.discovery_identity_expected = 0
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
                # Flag first: the state update's dispatch should
                # re-enable the bridge buttons in the same repaint.
                self.discovery_running = False
                self._update_discovery_state(
                    phase=DISCOVERY_PHASE_ERROR,
                    message=(
                        "No PC-Link detected and no inventory source found. "
                        "Install a PC-Link, drop your Nikobus .nkb export in "
                        "the config dir, or create nikobus_module_config.json."
                    ),
                    error="no_inventory_source",
                )
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
            # No state update fires on this path — nudge the bridge
            # buttons' availability directly so they don't stay greyed.
            async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Step 1: PC-Link probe failed (%s) — falling back to "
                "manual config files.",
                err,
            )
            # Same cleanup as the CancelledError path above: the library
            # sets ``discovery_running`` before the point where
            # ``start_inventory_discovery`` can raise. Without this, a
            # probe failure left the flag stuck True — polling suppressed
            # forever and every new scan rejected as "already running".
            # (nikobus-connect 0.27.1 also resets its own state on this
            # path; this is the integration's defence in depth for older
            # library versions.)
            self.discovery_running = False
            self._discovery_finished_event.set()
            async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)
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

        # Same eager claim as ``start_pc_link_inventory`` — close the
        # double-press race and grey the bridge buttons immediately.
        self.discovery_running = True
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
            async_dispatcher_send(self.hass, SIGNAL_DISCOVERY_STATE)
            raise
        except Exception as err:
            # Reset the flag BEFORE the state update so the dispatch it
            # fires re-enables the bridge buttons in the same repaint.
            self.discovery_running = False
            self._update_discovery_state(
                phase=DISCOVERY_PHASE_ERROR,
                message=f"Module scan failed: {err}",
                error=str(err),
            )
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
        * ``"areas"`` — each device into a Home Assistant Area = its
          room; per-key / IR child devices inherit their plate's Area.
        * ``"scenes"`` — name already-discovered Central Function scenes
          from the ``.nkb`` (never creates a scene from a button trigger).
        * ``"labels"`` — apply the integration's entity-class labels
          (``Nikobus Output`` / ``Nikobus Button`` / ``Nikobus Scene``)
          for one-click filtering. Additive only — user labels are
          never removed.

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

        name_map = data.addresses  # {ADDR: (name, room)} — name may be ""
        # {ADDR: Component.Number} — the index the Niko software shows as
        # "BP7" / "S1" (prefix is locale UI text, the number is the data).
        # getattr for older library versions without the field.
        numbers = getattr(data, "numbers", None) or {}

        # Scenes: match each named ``.nkb`` group to an already-discovered
        # Central Function by member set and apply its real name. We only
        # ever NAME an existing CF scene here — we never CREATE one.
        #
        # A named group's membership lives on its trigger input (the parser
        # resolves group → trigger → that input's output links → members),
        # so a group that isn't already a discovered CF broadcast is fired
        # by a real button on the bus. Surfacing it as a scene would just
        # duplicate a button the user already has, so we leave the button
        # untouched. A group with no on-bus trigger at all has no address to
        # activate, so there is nothing faithful to create for it either.
        # Only runs when the "scenes" category is selected.
        cf_name_by_addr: dict[str, str] = {}
        purged_nkb_scenes = False
        names_persisted = False
        if "scenes" in cats and self.cf_storage is not None:
            cf_store = self.cf_storage.data.get("nikobus_cf", {})
            # Migration: drop any button-duplicating scenes a previous
            # import created (``source == "nkb"``). Earlier versions turned
            # every named group fired by a real button into its own
            # ``nkb_scene`` CF, surfacing that button a second time as a
            # scene. We no longer create those, so clear out the stale ones
            # too — re-importing makes the duplication go away.
            stale = [
                addr
                for addr, cf in cf_store.items()
                if isinstance(cf, dict) and cf.get("source") == "nkb"
            ]
            for addr in stale:
                del cf_store[addr]
            purged_nkb_scenes = bool(stale)

            scene_by_members = {sc.members: sc.name for sc in data.scenes}
            for cf_addr, cf in cf_store.items():
                hit = scene_by_members.get(cf_member_set(cf))
                if hit:
                    cf_name_by_addr[str(cf_addr).upper()] = hit
                    # Persist the name onto the CF itself. The scene entity
                    # lives on its own ``cf_<addr>`` device (so it isn't
                    # merged into the trigger button's device), which the
                    # address-keyed registry rename below doesn't reach —
                    # so the name has to travel with the CF record.
                    if cf.get("name") != hit:
                        cf["name"] = hit
                        names_persisted = True
            if names_persisted or purged_nkb_scenes:
                await self.cf_storage.async_save()

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        entry_id = self.config_entry.entry_id

        def _display_for(key: str) -> tuple[str, str, str] | None:
            """``(display, plain, room)`` for a ``name_map`` address.

            * ``display`` — the suggested device name: ``Name (Room)``,
              prefixed with the Niko-software index (``7: …``) for
              button plates (6-hex addresses) so the HA device list
              stays cross-referenceable with the Nikobus application.
            * A plate the installer left unnamed falls back to its room
              (shown bare — no redundant ``Room (Room)``).
            * ``plain`` — the bare name (no index, no room), used to
              label the plate's per-key child devices.
            """
            name, room = name_map[key]
            base = name or room
            if not base:
                return None
            display = f"{base} ({room})" if name and room else base
            number = numbers.get(key)
            if number and len(key) == 6:
                display = f"{number}: {display}"
            return (display, base, room)

        def _lookup(device: Any) -> tuple[str, str, str] | None:
            for domain, ident in device.identifiers:
                if domain != DOMAIN:
                    continue
                key = ident.upper()
                # A CF scene lives on its own ``cf_<addr>`` device (split
                # out from the trigger button so it carries the scene's
                # name). HA doesn't repaint a device name once created, so
                # a scene matched on a *later* import would otherwise keep
                # its generic name — force it from the CF/scene name here,
                # never the trigger button's ``name_map`` entry.
                if key.startswith("CF_"):
                    cf_name = cf_name_by_addr.get(key[3:])
                    if cf_name:
                        return (cf_name, cf_name, "")
                    continue
                if key in name_map:
                    return _display_for(key)
                if key in cf_name_by_addr:
                    name = cf_name_by_addr[key]
                    return (name, name, "")
            return None

        do_names = "device_names" in cats
        do_areas = "areas" in cats
        matched: dict[str, str] = {}  # device_id -> display name
        matched_plain: dict[str, str] = {}  # device_id -> bare name (keys pass)
        matched_room: dict[str, str] = {}  # device_id -> room (child Areas)
        devices_named = areas_set = 0
        for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
            hit = _lookup(device)
            if hit is None:
                continue
            display, plain, room = hit
            matched[device.id] = display
            matched_plain[device.id] = plain
            matched_room[device.id] = room
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

        # Child devices inherit the parent plate's Area. Every key / IR
        # op-point of a plate is its own HA device, and none of them got
        # an Area from the pass above (their identifiers aren't in the
        # .nkb) — on a real install that left ~150 of ~215 Nikobus
        # devices outside the Area system entirely, invisible to Area
        # views and the auto-generated dashboard. A key lives where its
        # plate lives, so propagate the plate's room to all its children
        # (name-agnostic: IR op-points and renamed keys inherit too).
        if do_areas:
            for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
                room = matched_room.get(device.via_device_id)
                if not room or device.id in matched:
                    continue
                if overwrite or device.area_id is None:
                    area = area_reg.async_get_area_by_name(
                        room
                    ) or area_reg.async_create(room)
                    if device.area_id != area.id:
                        dev_reg.async_update_device(device.id, area_id=area.id)
                        areas_set += 1

        # Per-key child devices. Each op-point of a wall plate is its own
        # HA device (identifier = the key's bus address) named by the
        # library's generated "Push button 1A #N9A43A2" pattern — the raw
        # bus address is essential for identification while the plate is
        # anonymous, but reads as noise once the parent plate carries its
        # real .nkb name. Rename those to "<plate> Key <label>" (the
        # address stays available in the entity attributes for debugging).
        # The pattern gate means IR op-points, PC-Logic input keys and any
        # user-set names never match and are left untouched.
        keys_named = 0
        if do_names:
            for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
                plate = matched_plain.get(device.via_device_id)
                if not plate or device.id in matched:
                    continue
                m = _PUSH_BUTTON_NAME_RE.match(device.name or "")
                if m is None:
                    continue
                key_name = f"{plate} Key {m.group(1)}"
                if overwrite:
                    if device.name_by_user != key_name:
                        dev_reg.async_update_device(
                            device.id, name_by_user=key_name
                        )
                        keys_named += 1
                elif device.name != key_name:
                    dev_reg.async_update_device(device.id, name=key_name)
                    keys_named += 1

        # Persist the imported device names into the integration's own
        # stores (``nkb_name`` on button / op-point / module entries).
        # The registry writes above give the immediate effect, but the
        # device registry's ``name`` field is integration-owned: every
        # restart re-registers each device with
        # ``DeviceInfo(name=<generated>)`` and HA overwrites the field
        # with it — which silently reverted every non-overwrite import
        # on the next restart (3.11.0 field report). With ``nkb_name``
        # stored, the registration paths (register_wall_button_devices,
        # op_point_display_name, router.build_routing) re-assert the
        # imported name themselves, so the import survives restarts by
        # construction and ``name_by_user`` stays reserved for genuine
        # manual renames. The library's discovery merge upserts stored
        # entries field-by-field, so the extra key also survives
        # re-discovery.
        if do_names:
            store_changed = False
            buttons_store = (self.dict_button_data or {}).get("nikobus_button")
            if isinstance(buttons_store, dict):
                for addr, phys in buttons_store.items():
                    if not isinstance(phys, dict):
                        continue
                    key = str(addr).upper()
                    if key not in name_map:
                        continue
                    hit = _display_for(key)
                    if hit is None:
                        continue
                    display, plain, _room = hit
                    if phys.get("nkb_name") != display:
                        phys["nkb_name"] = display
                        store_changed = True
                    op_points = phys.get("operation_points")
                    if not isinstance(op_points, dict):
                        continue
                    for op in op_points.values():
                        if not isinstance(op, dict):
                            continue
                        # Same gate as the registry pass above: only
                        # library-generated wall-key names are eligible;
                        # IR op-points and PC-Logic input keys never
                        # match the pattern.
                        m = _PUSH_BUTTON_NAME_RE.match(
                            str(op.get("description") or "")
                        )
                        if m is None:
                            continue
                        key_name = f"{plain} Key {m.group(1)}"
                        prev = op.get("nkb_name")
                        if prev == key_name:
                            continue
                        op["nkb_name"] = key_name
                        store_changed = True
                        # Re-import with a changed plate name: the
                        # registry key pass above only matches devices
                        # still carrying the generated "Push button …"
                        # name, so a key already renamed by a previous
                        # import stays stale there. The integration-owned
                        # ``name`` field can only hold the generated name
                        # or a previous import's name (manual renames
                        # live in ``name_by_user``), so updating it when
                        # it equals the previous import is safe.
                        if prev and not overwrite:
                            dev = dev_reg.async_get_device(
                                identifiers={
                                    (DOMAIN, str(op.get("bus_address") or "").upper())
                                }
                            )
                            if dev is not None and dev.name == prev:
                                dev_reg.async_update_device(
                                    dev.id, name=key_name
                                )
                                keys_named += 1
            if store_changed:
                await self.button_storage.async_save()

            modules_changed = False
            if self.module_storage is not None:
                modules_store = self.module_storage.data.get("nikobus_module")
                if isinstance(modules_store, dict):
                    for addr, module in modules_store.items():
                        if not isinstance(module, dict):
                            continue
                        key = str(addr).upper()
                        if key not in name_map:
                            continue
                        hit = _display_for(key)
                        if hit is None:
                            continue
                        display = hit[0]
                        if module.get("nkb_name") != display:
                            module["nkb_name"] = display
                            modules_changed = True
                if modules_changed:
                    await self.module_storage.async_save()
                    # ``dict_module_data`` holds copies of the store
                    # entries — refresh it so the live view carries the
                    # imported names too.
                    self._rebuild_dict_module_data()

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

        # Enable outputs the bus scan left hidden. The register scan reads
        # link records, never per-channel text — Nikobus modules don't
        # store channel names on the bus at all — so every channel starts
        # as the ``"not_in_use output_N"`` placeholder router.py uses to
        # skip entity creation. The rename loop above can only rename an
        # entity that already exists, so a channel with no entity yet had
        # no way to ever leave that state short of the manual "Customize a
        # module" flow — the .nkb already carries the real name here
        # (``data.outputs``), so use it to unlock the channel directly
        # rather than only relabelling something already visible.
        outputs_enabled = 0
        module_storage_changed = False
        if "channel_names" in cats and data.outputs and self.module_storage is not None:
            modules = self.module_storage.data.get("nikobus_module")
            if isinstance(modules, dict):
                for (addr, ch), nm in data.outputs.items():
                    if not nm:
                        continue
                    module = modules.get(addr)
                    if not isinstance(module, dict):
                        continue
                    channels = module.get("channels")
                    if not isinstance(channels, list) or not (1 <= ch <= len(channels)):
                        continue
                    channel_info = channels[ch - 1]
                    if not isinstance(channel_info, dict):
                        continue
                    if channel_info.get("entity_type") == "disabled":
                        continue  # user explicitly hid this channel — leave it
                    if not str(channel_info.get("description", "")).startswith(
                        "not_in_use"
                    ):
                        continue  # already enabled — the rename loop above covers it
                    channel_info["description"] = nm
                    outputs_enabled += 1
                    module_storage_changed = True
            if module_storage_changed:
                await self.module_storage.async_save()
                self._rebuild_dict_module_data()

        # Labels — one-click filtering by entity class in every HA table
        # and target picker. Purely additive: a label is only ever added
        # (never removed), so user-applied labels are untouched and
        # removing one of ours by hand sticks until the next labels
        # import. Latch switches and the bridge's own entities are
        # deliberately unlabelled — they fit none of the three classes.
        labels_set = 0
        if "labels" in cats:
            from homeassistant.helpers import label_registry as lr

            label_reg = lr.async_get(self.hass)
            label_ids: dict[str, str] = {}

            def _label_id(label_name: str) -> str:
                cached = label_ids.get(label_name)
                if cached is None:
                    entry = label_reg.async_get_label_by_name(
                        label_name
                    ) or label_reg.async_create(label_name)
                    cached = entry.label_id
                    label_ids[label_name] = cached
                return cached

            for ent in entities:
                domain = ent.entity_id.split(".", 1)[0]
                uid = str(ent.unique_id or "")
                if uid.startswith("nikobus_push_button_") or domain == "binary_sensor":
                    label_name = "Nikobus Button"
                elif domain == "scene":
                    label_name = "Nikobus Scene"
                elif domain in ("light", "cover") or (
                    domain == "switch"
                    and not uid.startswith("nikobus_input_switch_")
                ):
                    label_name = "Nikobus Output"
                else:
                    continue
                label_id = _label_id(label_name)
                current = set(ent.labels or ())
                if label_id not in current:
                    ent_reg.async_update_entity(
                        ent.entity_id, labels=current | {label_id}
                    )
                    labels_set += 1

        _LOGGER.info(
            "Imported .nkb from %s (overwrite=%s, categories=%s): %d devices, "
            "%d key devices, %d device-entities, %d channels, %d outputs "
            "enabled, %d areas, %d scenes named",
            path.name,
            overwrite,
            sorted(cats),
            devices_named,
            keys_named,
            entities_named,
            channels_named,
            outputs_enabled,
            areas_set,
            len(cf_name_by_addr),
        )

        # The surfaced entity set changed — reload so platforms rebuild:
        # names just applied surface previously-hidden (unnamed) button-backed
        # light-scenes, purging stale duplicates tears theirs down, and newly
        # enabled outputs need their entities created for the first time.
        if purged_nkb_scenes or names_persisted or module_storage_changed:
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

        return {
            "devices": devices_named,
            "keys": keys_named,
            "entities": entities_named,
            "channels": channels_named,
            "outputs_enabled": outputs_enabled,
            "areas": areas_set,
            "scenes": len(cf_name_by_addr),
            "labels": labels_set,
        }

