"""What the modules are programmed to do, and the PC-Link's clock.

Everything here reads, with one exception the library allows: setting
the PC-Link's clock. A module's programming is never written and no
module is put in programming mode.

Three things are kept: how far the PC-Link's clock is from Home
Assistant's, the result of the last check of each output module (its
memory flag, its type, the checksum of its memory against the one it
reports), and the checksum each module had when its links were read. A
module whose checksum has moved since was reprogrammed, so its links
here are out of date: that is a repair, fixed by a discovery.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from nikobus_connect import NikobusError
from nikobus_connect.api import MODULE_IMAGE_SIZES, predict_module_crc
from nikobus_connect.protocol import family_matches, family_name

from .const import DOMAIN

if TYPE_CHECKING:
    from .hub import NikobusHub

_LOGGER = logging.getLogger(__name__)

BACKUP_FOLDER = "nikobus_ng_backup"

OK = "ok"
PROBLEM = "problem"
UNKNOWN = "unknown"
HEALTH = [OK, PROBLEM, UNKNOWN]

ISSUE_MEMORY_ERROR = "module_memory_error"
ISSUE_CHECKSUM = "module_checksum_mismatch"
ISSUE_TYPE = "module_type_mismatch"
ISSUE_REPROGRAMMED = "module_reprogrammed"


@dataclass(slots=True)
class ModuleCheck:
    """What one module answered to a check."""

    address: str
    module_type: str
    memory_error: bool | None = None
    family: str | None = None
    family_ok: bool | None = None
    links: int | None = None
    checksum_ok: bool | None = None
    reported_checksum: int | None = None
    computed_checksum: int | None = None
    image_bytes: int | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        """``ok``, ``problem``, or ``unknown`` when the module did not answer."""
        if self.error is not None or self.memory_error is None:
            return UNKNOWN
        if self.memory_error or self.checksum_ok is False or self.family_ok is False:
            return PROBLEM
        return OK

    def as_dict(self) -> dict[str, Any]:
        """The check as a plain dict, with its status."""
        return {**asdict(self), "status": self.status}


def _write_backup(folder: Path, images: dict[str, bytes], summary: dict[str, Any]) -> None:
    """Write every file under a temporary name first: no half-written backup."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in images.items():
        partial = folder / f"{name}.tmp"
        partial.write_bytes(data)
        partial.replace(folder / name)
    partial = folder / "summary.json.tmp"
    partial.write_text(json.dumps(summary, indent=2))
    partial.replace(folder / "summary.json")


class Maintenance:
    """The clock, the checks and the backup of one installation."""

    def __init__(self, hub: NikobusHub) -> None:
        self._hub = hub
        self.running = False
        self.clock_drift: float | None = None
        self.clock_read_at: datetime | None = None
        self.checks: dict[str, ModuleCheck] = {}
        self.checked_at: datetime | None = None
        self.reprogrammed: list[str] = []
        self.backup_path: str | None = None

    # --- what it works on -------------------------------------------------------

    @property
    def pc_link_address(self) -> str | None:
        """The PC-Link of the inventory; ``None`` when there is none."""
        return self._hub.inventory.pc_link_address()

    def _modules(self, addresses: list[str] | None = None) -> list[tuple[str, str]]:
        """``(address, type)`` of the modules whose memory layout is known."""
        wanted = {address.upper() for address in addresses} if addresses else None
        return [
            (module.address, module.module_type)
            for module in self._hub.inventory.output_modules()
            if module.module_type in MODULE_IMAGE_SIZES and (wanted is None or module.address in wanted)
        ]

    @property
    def health(self) -> str:
        """The worst status of the modules checked so far."""
        statuses = {check.status for check in self.checks.values()}
        if PROBLEM in statuses:
            return PROBLEM
        return OK if statuses == {OK} else UNKNOWN

    def report(self, addresses: list[str] | None = None) -> dict[str, Any]:
        """The last check of each module, or of the ones asked for."""
        wanted = {address.upper() for address in addresses} if addresses else None
        return {
            "health": self.health,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "modules": {
                address: check.as_dict()
                for address, check in sorted(self.checks.items())
                if wanted is None or address in wanted
            },
        }

    # --- guards -----------------------------------------------------------------

    def _require_connection(self) -> None:
        if not self._hub.connected:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_connected")

    def _require_free_bus(self) -> None:
        self._require_connection()
        if self._hub.discovery.running:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="discovery_running")
        if self.running:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="maintenance_running")

    def _require_pc_link(self) -> str:
        if (address := self.pc_link_address) is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_pc_link_known")
        return address

    def _set_running(self, running: bool) -> None:
        self.running = running
        self._hub.notify()

    # --- the PC-Link's clock ----------------------------------------------------

    async def async_read_clock(self) -> float | None:
        """Read the PC-Link's clock; its distance from Home Assistant's, in seconds."""
        self._require_connection()
        address = self._require_pc_link()
        try:
            clock = await self._hub.api.get_pc_link_time(address)
        except ValueError:
            # It answered, with a clock that was never set.
            self.clock_drift = None
        except (NikobusError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"address": address, "error": str(err)},
            ) from err
        else:
            # The PC-Link keeps local wall time and knows no time zone.
            self.clock_drift = round((clock - dt_util.now().replace(tzinfo=None)).total_seconds())
        self.clock_read_at = dt_util.utcnow()
        self._hub.notify()
        return self.clock_drift

    async def async_set_clock(self) -> None:
        """Give the PC-Link Home Assistant's local time, and read it back."""
        self._require_free_bus()
        address = self._require_pc_link()
        try:
            await self._hub.api.set_pc_link_time(
                address, dt_util.now().replace(tzinfo=None, microsecond=0)
            )
        except (NikobusError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"address": address, "error": str(err)},
            ) from err
        await self.async_read_clock()

    # --- checks and backup ------------------------------------------------------

    async def _async_check(self, address: str, module_type: str) -> tuple[ModuleCheck, bytes | None]:
        """Ask one module for its status and read its memory against its checksum."""
        check = ModuleCheck(address, module_type)
        image: bytes | None = None
        api = self._hub.api
        try:
            status = await api.get_module_status(address)
            check.memory_error = status.eeprom_error
            check.links = status.record_count_a
            signature = getattr(status, "type_code", None)
            if isinstance(signature, int):
                check.family = family_name(signature)
                check.family_ok = family_matches(module_type, signature)
            image = await api.read_module_memory(address, module_type)
            check.image_bytes = len(image)
            check.checksum_ok, check.reported_checksum, check.computed_checksum = (
                await api.verify_module_memory(address, module_type, image)
            )
        except (NikobusError, TimeoutError, ValueError) as err:
            check.error = str(err) or type(err).__name__
            _LOGGER.warning("Module %s could not be checked: %s", address, check.error)
        return check, image

    def _file_issues(self, check: ModuleCheck) -> None:
        for key, active in (
            (ISSUE_MEMORY_ERROR, check.memory_error is True),
            (ISSUE_CHECKSUM, check.checksum_ok is False),
            (ISSUE_TYPE, check.family_ok is False),
        ):
            issue_id = f"{key}_{check.address.lower()}"
            if active:
                ir.async_create_issue(
                    self._hub.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=key,
                    translation_placeholders={
                        "module": self._hub.inventory.module_label(check.address),
                        "module_type": check.module_type.replace("_", " "),
                        "family": (check.family or "unknown").replace("_", " "),
                    },
                )
            elif check.error is None:
                ir.async_delete_issue(self._hub.hass, DOMAIN, issue_id)

    async def _async_check_all(self, addresses: list[str] | None) -> dict[str, bytes]:
        self._require_free_bus()
        self._set_running(True)
        images: dict[str, bytes] = {}
        try:
            for address, module_type in self._modules(addresses):
                check, image = await self._async_check(address, module_type)
                self.checks[address] = check
                self._file_issues(check)
                if image is not None:
                    images[f"{address}_{module_type}.nkm"] = image
                if check.checksum_ok and check.reported_checksum is not None:
                    # A memory that checks out is the programming now known.
                    await self._async_record(address, module_type, check.reported_checksum)
            self.checked_at = dt_util.utcnow()
            self._hub.inventory.schedule_save()
        finally:
            self._set_running(False)
        return images

    def start(self, *, backup: bool) -> None:
        """Run a check or a backup in the background. Raises when the bus is taken."""
        self._require_free_bus()

        async def _run() -> None:
            try:
                await (self.async_backup() if backup else self.async_verify())
            except HomeAssistantError as err:
                _LOGGER.error("The programming %s stopped: %s", "backup" if backup else "check", err)

        self._hub.entry.async_create_background_task(
            self._hub.hass, _run(), name=f"{DOMAIN}_{'backup' if backup else 'verify'}"
        )

    async def async_verify(self, addresses: list[str] | None = None) -> dict[str, Any]:
        """Check every output module, or the ones named. Minutes on a real bus."""
        await self._async_check_all(addresses)
        return self.report(addresses)

    async def async_backup(self, addresses: list[str] | None = None) -> dict[str, Any]:
        """Check the modules and keep the memory of each in a folder of the config directory."""
        images = await self._async_check_all(addresses)
        summary = self.report(addresses)
        folder = Path(
            self._hub.hass.config.path(BACKUP_FOLDER, dt_util.now().strftime("%Y%m%d-%H%M%S"))
        )
        try:
            await self._hub.hass.async_add_executor_job(_write_backup, folder, images, summary)
        except OSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="backup_failed",
                translation_placeholders={"path": str(folder), "error": str(err)},
            ) from err
        self.backup_path = str(folder)
        self._hub.notify()
        return {"path": str(folder), "files": sorted(images), **summary}

    # --- reprogrammed since the links were read ---------------------------------

    async def _async_record(self, address: str, module_type: str, checksum: int) -> None:
        """Keep a module's checksum, and the bytes a dimmer rewrites by itself."""
        volatile: dict[int, bytes] = {}
        try:
            volatile = await self._hub.api.read_module_volatile(address, module_type)
        except (NikobusError, TimeoutError) as err:
            _LOGGER.debug("Self-written memory of %s not read: %s", address, err)
        self._hub.inventory.programming[address] = {
            "checksum": checksum,
            "recorded_at": dt_util.utcnow().isoformat(),
            "volatile": {str(start): data.hex() for start, data in volatile.items()},
        }

    async def async_check_reprogrammed(self) -> list[str]:
        """Compare each module's checksum with the one kept; one frame a module.

        A module with none kept gets its present one recorded. A dimmer
        rewrites a few bytes of its own memory when power drops: when
        those bytes alone explain the new checksum, nothing was
        reprogrammed and the new one is kept.
        """
        if self._hub.busy or not self._hub.connected:
            return list(self.reprogrammed)
        api = self._hub.api
        programming = self._hub.inventory.programming
        reprogrammed: list[str] = []
        for address, module_type in self._modules():
            if self._hub.busy or not self._hub.connected:
                return list(self.reprogrammed)
            try:
                checksum = await api.get_module_crc(address)
            except (NikobusError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Checksum of module %s not read: %s", address, err)
                if address in self.reprogrammed:
                    reprogrammed.append(address)
                continue
            kept = programming.get(address)
            if kept is None:
                await self._async_record(address, module_type, checksum)
                continue
            if kept.get("checksum") != checksum and await self._async_moved_by_itself(
                address, module_type, kept, checksum
            ):
                await self._async_record(address, module_type, checksum)
                continue
            issue_id = f"{ISSUE_REPROGRAMMED}_{address.lower()}"
            if kept.get("checksum") == checksum:
                ir.async_delete_issue(self._hub.hass, DOMAIN, issue_id)
                continue
            reprogrammed.append(address)
            ir.async_create_issue(
                self._hub.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_REPROGRAMMED,
                translation_placeholders={"module": self._hub.inventory.module_label(address)},
                data={"entry_id": self._hub.entry.entry_id},
            )
        self.reprogrammed = reprogrammed
        self._hub.inventory.schedule_save()
        self._hub.notify()
        return list(reprogrammed)

    async def _async_moved_by_itself(
        self, address: str, module_type: str, kept: dict[str, Any], checksum: int
    ) -> bool:
        stored = kept.get("volatile")
        if not isinstance(stored, dict) or not stored:
            return False
        try:
            before = {int(start): bytes.fromhex(data) for start, data in stored.items()}
            now = await self._hub.api.read_module_volatile(address, module_type)
        except (NikobusError, TimeoutError, ValueError, TypeError):
            return False
        return predict_module_crc(module_type, int(kept.get("checksum") or 0), before, now) == checksum

    def forget_checksums(self) -> None:
        """After a discovery: the links are fresh, so the next check records anew."""
        for address in list(self._hub.inventory.programming):
            ir.async_delete_issue(
                self._hub.hass, DOMAIN, f"{ISSUE_REPROGRAMMED}_{address.lower()}"
            )
        self._hub.inventory.programming.clear()
        self.reprogrammed = []

    async def async_scheduled_clock(self, _now: datetime | None = None) -> None:
        """The hourly read of the clock: never raises."""
        if self.pc_link_address is None or self._hub.busy or not self._hub.connected:
            return
        try:
            await self.async_read_clock()
        except HomeAssistantError as err:
            _LOGGER.debug("PC-Link clock not read: %s", err)

    async def async_scheduled_check(self, _now: datetime | None = None) -> None:
        """The daily look at the checksums: never raises."""
        try:
            await self.async_check_reprogrammed()
        except Exception:
            _LOGGER.exception("Could not check whether a module was reprogrammed")
