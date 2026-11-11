"""One action that discovers the whole installation.

The library reads an installation in two passes: the PC-Link's
inventory (which modules and plates exist), then every output module's
own link table (which key drives which output, and the scenes). Here
they run one after the other, started by one button or by the first
setup, and end with a reload so the entities appear.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from nikobus_connect.discovery import PHASE_REGISTER_SCAN, InventoryQueryType
from nikobus_connect.discovery.store import flatten_cf_broadcasts, is_button_backed_cf

from .const import (
    DOMAIN,
    INVENTORY_CLOSE_TIMEOUT,
    INVENTORY_PROBE_TIMEOUT,
    ISSUE_CORRUPT_LINKS,
)

if TYPE_CHECKING:
    from .hub import NikobusHub

_LOGGER = logging.getLogger(__name__)

IDLE = "idle"
INVENTORY = "inventory"
LINKS = "links"
FINALIZING = "finalizing"
ERROR = "error"
PHASES = [IDLE, INVENTORY, LINKS, FINALIZING, ERROR]

#: Gateways that answer the probe but hold no inventory.
_NO_INVENTORY = ("feedback_module", "pc_logic")
# Where each pass starts on the progress scale.
_LINKS_FROM, _FINALIZING_FROM = 30, 95


@dataclass(slots=True)
class DiscoveryStatus:
    """What the status sensor shows."""

    phase: str = IDLE
    progress: int = 0
    module: str | None = None
    error: str | None = None


class DiscoveryRunner:
    """Runs the inventory and the link scan, and reports on them."""

    def __init__(self, hub: NikobusHub) -> None:
        self._hub = hub
        self.status = DiscoveryStatus()
        self.running = False
        self._task: asyncio.Task[None] | None = None
        self._first_answer = asyncio.Event()
        self._pass_finished = asyncio.Event()
        self._scenes: dict[str, dict[str, Any]] = {}
        self._corrupt: set[str] = set()

    def start(self, module: str | None = None) -> None:
        """Start a discovery in the background. Raises when one is running.

        With ``module``, only that module's links are read again: what a
        reprogrammed module needs, in a fraction of the time.
        """
        if self.running:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="discovery_running")
        if not self._hub.connected:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_connected")
        if self._hub.maintenance.running:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="maintenance_running")
        self.running = True
        self._task = self._hub.entry.async_create_background_task(
            self._hub.hass, self._async_run(module), name=f"{DOMAIN}_discovery"
        )

    async def async_wait(self) -> None:
        """Wait for the discovery in progress, if any."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _async_run(self, module: str | None) -> None:
        hub = self._hub
        hub.apply_write_guard()
        try:
            if module is None and not hub.inventory.from_project:
                # A project file's inventory stands in for the PC-Link's.
                await self._async_inventory()
            await self._async_links(module)
            await self._async_finalize(module)
        except HomeAssistantError as err:
            _LOGGER.error("Discovery stopped: %s", err)
            self._set(ERROR, error=err.translation_key or str(err))
            return
        except Exception:
            _LOGGER.exception("Discovery failed")
            self._set(ERROR, error="unknown")
            return
        finally:
            self.running = False
            hub.discovery_running = False
            hub.apply_write_guard()
            hub.notify()
        self._set(IDLE, progress=100)
        hub.hass.config_entries.async_schedule_reload(hub.entry.entry_id)

    async def _async_inventory(self) -> None:
        """Ask the PC-Link which modules and plates exist."""
        hub = self._hub
        self._set(INVENTORY)
        if hub.connection.gateway_family in _NO_INVENTORY:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_pc_link")
        self._first_answer.clear()
        self._pass_finished.clear()
        hub.discovery_running = True
        await hub.engine.start_inventory_discovery()
        try:
            async with asyncio.timeout(INVENTORY_PROBE_TIMEOUT):
                await self._first_answer.wait()
        except TimeoutError:
            # The library closes the pass by itself; let it, then report.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(INVENTORY_CLOSE_TIMEOUT):
                    await self._pass_finished.wait()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_pc_link"
            ) from None
        await self._pass_finished.wait()
        hub.inventory.changed()
        if hub.inventory.is_empty:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_modules")
        hub.allocate_states()

    async def _async_links(self, module: str | None) -> None:
        """Read the link table of every module, or of one."""
        hub = self._hub
        self._set(LINKS, progress=_LINKS_FROM)
        self._pass_finished.clear()
        hub.discovery_running = True
        await hub.engine.query_module_inventory(module or "ALL")
        await self._pass_finished.wait()

    async def _async_finalize(self, module: str | None) -> None:
        """Keep the scenes the scan classified, report unreadable link tables, and save."""
        hub = self._hub
        self._set(FINALIZING, progress=_FINALIZING_FROM)
        hub.inventory.scenes = (
            dict(self._scenes) if module is None else self._scenes_after(module, hub.inventory.scenes)
        )
        scanned = [module] if module else sorted(hub.inventory.addresses())
        for address in scanned:
            issue_id = f"{ISSUE_CORRUPT_LINKS}_{address.lower()}"
            if address in self._corrupt:
                ir.async_create_issue(
                    hub.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_CORRUPT_LINKS,
                    translation_placeholders={"module": hub.inventory.module_label(address)},
                )
            else:
                ir.async_delete_issue(hub.hass, DOMAIN, issue_id)
        # The links are fresh: the next look at the checksums records them anew.
        hub.maintenance.forget_checksums(module)
        await hub.inventory.async_save()

    def _scenes_after(self, module: str, known: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """The scenes once one module was read again.

        A light scene on a key is classified from every plate's links, so
        the fresh ones stand. A central function with an address of its
        own is classified from the links read in this run, which are this
        module's only: its members on other modules are kept as they were.
        """
        scenes = {address: scene for address, scene in self._scenes.items() if is_button_backed_cf(scene)}
        fresh = {address: scene for address, scene in self._scenes.items() if address not in scenes}
        kept = {address: scene for address, scene in known.items() if not is_button_backed_cf(scene)}
        for address in sorted({*fresh, *kept}):
            elsewhere = [
                output
                for output in (kept.get(address) or {}).get("outputs") or []
                if str(output.get("module_address") or "").upper() != module
            ]
            outputs = [*elsewhere, *((fresh.get(address) or {}).get("outputs") or [])]
            if outputs:
                scenes[address] = {**(fresh.get(address) or kept[address]), "outputs": outputs}
        return scenes

    def _set(self, phase: str, *, progress: int = 0, error: str | None = None) -> None:
        self.status = DiscoveryStatus(phase=phase, progress=progress, error=error)
        self._hub.notify()

    # --- what the library reports -------------------------------------------------

    def on_progress(self, progress: Any) -> None:
        """Turn the library's counters into one percentage."""
        if not self.running or progress.phase != PHASE_REGISTER_SCAN or not progress.module_total:
            return
        module_done = (progress.registers_sent / progress.register_total) if progress.register_total else 0
        done = (max(progress.module_index, 1) - 1 + min(module_done, 1)) / progress.module_total
        percent = _LINKS_FROM + int((_FINALIZING_FROM - _LINKS_FROM) * min(done, 1))
        if percent != self.status.progress or progress.module_address != self.status.module:
            self.status = DiscoveryStatus(phase=LINKS, progress=percent, module=progress.module_address)
            self._hub.notify()

    async def async_on_finished(self, **_kwargs: Any) -> None:
        """A pass of the library is over."""
        # The library clears what it classified and flagged as soon as this returns.
        self._scenes = flatten_cf_broadcasts(self._hub.engine.discovered_cf_broadcasts)
        self._corrupt = {str(address).upper() for address in self._hub.engine.corrupt_link_tables}
        self._pass_finished.set()

    def on_inventory_frame(self, message: str) -> None:
        """A ``$18`` frame: an inventory record while the PC-Link is being read."""
        if self.running and self._hub.inventory_query_type is InventoryQueryType.PC_LINK:
            self._hub.engine.handle_device_address_inventory(message)

    async def async_on_answer_frame(self, message: str) -> None:
        """A ``$2E`` / ``$1E`` frame: a memory block one of the passes asked for."""
        if not self.running:
            return
        if self._hub.inventory_query_type is InventoryQueryType.MODULE:
            await self._hub.engine.parse_module_inventory_response(message)
        elif self._hub.inventory_query_type is InventoryQueryType.PC_LINK:
            self._first_answer.set()
            await self._hub.engine.parse_inventory_response(message)
