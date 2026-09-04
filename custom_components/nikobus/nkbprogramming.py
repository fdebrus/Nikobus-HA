"""What the Nikobus modules are programmed to do — read-only maintenance.

Groups the features that read a module's own programming rather than
its live state: the PC-Link clock, the per-module status / integrity
check, the programming backup, and the cover run times derived from
the roller links. Everything here is read-only on the bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from nikobus_connect.api import MODULE_CRC_UNKNOWN, MODULE_IMAGE_SIZES
from nikobus_connect.discovery.feedback_decoder import (
    FeedbackImage,
    FeedbackLed,
    decode_feedback_image,
)
from nikobus_connect.discovery.fileio import find_module
from nikobus_connect.exceptions import NikobusError
from nikobus_connect.protocol import (
    FUNC_READ_BLOCK8,
    FUNC_READ_BLOCK16,
    make_block_index_args,
)

from .const import (
    DOMAIN,
    ISSUE_MODULE_CRC_MISMATCH,
    ISSUE_MODULE_EEPROM_ERROR,
    SIGNAL_DISCOVERY_STATE,
)
from .router import iter_operation_points

_LOGGER = logging.getLogger(__name__)

BACKUP_DIR = "nikobus_backup"
LED_SOURCE_FEEDBACK = "feedback_module"

# Row order of the LED slots inside one push-button module group, by
# number of keys on the plate (the order the Nikobus application lists
# them). Used only when the link table cannot single out the key.
_LED_ROW_KEYS: dict[int, tuple[str, ...]] = {
    1: ("1A",),
    2: ("1B", "1A"),
    4: ("1D", "1C", "1B", "1A"),
    8: ("2D", "2C", "2B", "2A", "1D", "1C", "1B", "1A"),
}

HEALTH_OK = "ok"
HEALTH_PROBLEM = "problem"
HEALTH_UNKNOWN = "unknown"

# Roller link modes that drive a shutter for their configured run time.
_ROLLER_UP_MODES = ("M02", "M06")
_ROLLER_DOWN_MODES = ("M03", "M07")
_ROLLER_BOTH_MODES = ("M01",)

_RUN_TIME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(s|m)\b")


def parse_run_time_label(label: Any) -> float | None:
    """Seconds encoded in a decoded timer label (``"30 s"``, ``"1 m"``).

    Labels without a duration (``OFF``) and sub-second impulses return
    ``None`` — they don't describe a travel.
    """
    if not isinstance(label, str):
        return None
    match = _RUN_TIME_RE.search(label)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    seconds = value * 60 if match.group(2) == "m" else value
    return seconds if seconds >= 1 else None


def link_run_time(
    button_data: dict[str, Any] | None,
    module_address: str,
    channel: int,
    direction: str,
) -> float | None:
    """Longest run time programmed into the roller links of one output.

    Scans every button link that targets ``module_address``/``channel``
    and returns the largest run time among the links whose mode moves
    the shutter in ``direction`` (``"up"`` or ``"down"``); ``None`` when
    no link carries one. This is the time the module itself keeps the
    relay engaged, so it is the physical truth for the position model.
    """
    modes = _ROLLER_BOTH_MODES + (
        _ROLLER_UP_MODES if direction == "up" else _ROLLER_DOWN_MODES
    )
    target = str(module_address).upper()
    best: float | None = None
    buttons = (button_data or {}).get("nikobus_button") or {}
    for _addr, _key, op_point, _ in iter_operation_points(buttons):
        for module_address_, channel_, output in iter_link_outputs(op_point):
            if module_address_ != target or channel_ != channel:
                continue
            mode = str(output.get("mode") or "")
            if not mode.startswith(modes):
                continue
            seconds = parse_run_time_label(output.get("t1"))
            if seconds is not None and (best is None or seconds > best):
                best = seconds
    return best


def iter_link_outputs(op_point: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    """``(module_address, channel, output)`` for every link of an op-point.

    Discovery stores ``linked_modules`` as ``[{module_address, outputs:
    [{channel, mode, t1, t2}]}]``; a flat ``{module_address, channel,
    mode, t1}`` entry is accepted as well.
    """
    found: list[tuple[str, int, dict[str, Any]]] = []
    for link in op_point.get("linked_modules") or []:
        if not isinstance(link, dict):
            continue
        module_address = str(link.get("module_address") or "").upper()
        if not module_address:
            continue
        outputs = link.get("outputs")
        if isinstance(outputs, list):
            candidates = [out for out in outputs if isinstance(out, dict)]
        else:
            candidates = [link]
        for out in candidates:
            try:
                channel = int(out.get("channel"))
            except (TypeError, ValueError):
                continue
            found.append((module_address, channel, out))
    return found


@dataclass
class ModuleCheck:
    """Result of one module's status / integrity check."""

    address: str
    module_type: str
    description: str
    eeprom_error: bool | None = None
    record_count_a: int | None = None
    record_count_b: int | None = None
    crc_ok: bool | None = None
    module_crc: int | None = None
    computed_crc: int | None = None
    image_bytes: int | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error is not None or self.eeprom_error is None:
            return HEALTH_UNKNOWN
        if self.eeprom_error or self.crc_ok is False:
            return HEALTH_PROBLEM
        return HEALTH_OK

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status
        return data


class NikobusProgramming:
    """Read-only maintenance over the modules' programming."""

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._lock = asyncio.Lock()
        self.running = False
        self.clock: datetime | None = None
        self.clock_read_at: datetime | None = None
        self.clock_drift_seconds: float | None = None
        self.checks: dict[str, ModuleCheck] = {}
        self.last_check_at: datetime | None = None
        self.last_backup_path: str | None = None
        self.last_backup_at: datetime | None = None

    # -- inventory helpers -------------------------------------------------

    def _modules(self) -> list[tuple[str, str, dict[str, Any]]]:
        """``(address, module_type, entry)`` over the coordinator's grouped view.

        ``dict_module_data`` is keyed by module type, then by address.
        """
        data = self._coordinator.dict_module_data or {}
        out: list[tuple[str, str, dict[str, Any]]] = []
        for module_type, group in data.items():
            if not isinstance(group, dict):
                continue
            for address, entry in group.items():
                if isinstance(entry, dict):
                    out.append(
                        (str(address).upper(), str(entry.get("module_type") or module_type), entry)
                    )
        return out

    def pc_link_address(self) -> str | None:
        """Address of the PC-Link, or ``None`` when no inventory names one."""
        for address, module_type, _entry in self._modules():
            if module_type == "pc_link":
                return address
        return None

    def output_modules(self) -> list[tuple[str, str, str]]:
        """``(address, module_type, description)`` of every module with an image."""
        return [
            (address, module_type, str(entry.get("description") or address))
            for address, module_type, entry in self._modules()
            if module_type in MODULE_IMAGE_SIZES
        ]

    def link_run_time(self, module_address: str, channel: int, direction: str) -> float | None:
        return link_run_time(self._coordinator.dict_button_data, module_address, channel, direction)

    @property
    def health(self) -> str:
        if not self.checks:
            return HEALTH_UNKNOWN
        statuses = {check.status for check in self.checks.values()}
        if HEALTH_PROBLEM in statuses:
            return HEALTH_PROBLEM
        if HEALTH_UNKNOWN in statuses and HEALTH_OK not in statuses:
            return HEALTH_UNKNOWN
        return HEALTH_OK

    def _set_running(self, running: bool) -> None:
        """Flip the busy flag and repaint the bridge buttons' availability."""
        self.running = running
        async_dispatcher_send(self._hass, SIGNAL_DISCOVERY_STATE)

    # -- guards --------------------------------------------------------------

    def _api(self) -> Any:
        api = self._coordinator.api
        if api is None or not self._coordinator.nikobus_connection.is_connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            )
        return api

    def _require_pc_link(self) -> str:
        address = self.pc_link_address()
        if address is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_pc_link_known"
            )
        return address

    def _acquire(self) -> None:
        if self._coordinator.discovery_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="discovery_already_running"
            )
        if self.running:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="maintenance_running"
            )

    # -- PC-Link clock -----------------------------------------------------

    async def async_read_clock(self) -> datetime | None:
        """Read the PC-Link clock; ``None`` when it can't be read."""
        api = self._api()
        address = self._require_pc_link()
        try:
            naive = await api.get_pc_link_time(address)
        except ValueError:
            # The controller answered but its clock was never set.
            _LOGGER.warning("PC-Link %s reports an unset clock", address)
            self.clock = None
            self.clock_drift_seconds = None
            self.clock_read_at = dt_util.now()
            return None
        now = dt_util.now()
        self.clock = naive.replace(tzinfo=now.tzinfo)
        self.clock_drift_seconds = (naive - now.replace(tzinfo=None)).total_seconds()
        self.clock_read_at = now
        return self.clock

    async def async_sync_clock(self) -> datetime:
        """Write Home Assistant's local time into the PC-Link and re-read it."""
        api = self._api()
        address = self._require_pc_link()
        now = dt_util.now()
        await api.set_pc_link_time(address, now.replace(tzinfo=None, microsecond=0))
        _LOGGER.info("PC-Link %s clock set to %s", address, now.isoformat(timespec="seconds"))
        clock = await self.async_read_clock()
        return clock or now

    # -- status / integrity ------------------------------------------------

    async def _check_module(
        self, api: Any, address: str, module_type: str, description: str, *, image: bool
    ) -> tuple[ModuleCheck, bytes | None]:
        check = ModuleCheck(address, module_type, description)
        data: bytes | None = None
        crc_unknown = module_type in MODULE_CRC_UNKNOWN
        try:
            try:
                status = await api.get_module_status(address)
            except Exception:
                # A module whose CRC coverage is unknown may not answer
                # the status query either; its image is still worth
                # reading. Every other module must answer.
                if not crc_unknown:
                    raise
                _LOGGER.debug("Module %s did not answer the status query", address)
            else:
                check.eeprom_error = status.eeprom_error
                check.record_count_a = status.record_count_a
                # 0xFF = "no second table" on switch / roller modules.
                check.record_count_b = (
                    None if status.record_count_b == 0xFF else status.record_count_b
                )
            if image:
                data = await api.read_module_memory(address, module_type)
                check.image_bytes = len(data)
                if not crc_unknown:
                    check.crc_ok, check.module_crc, check.computed_crc = (
                        await api.verify_module_memory(address, module_type, data)
                    )
        except Exception as err:  # noqa: BLE001 - one module's failure must not abort the run
            check.error = str(err) or err.__class__.__name__
            _LOGGER.warning("Programming check of module %s failed: %s", address, check.error)
        return check, data

    def _apply_issues(self, check: ModuleCheck) -> None:
        for key, active in (
            (ISSUE_MODULE_EEPROM_ERROR, check.eeprom_error is True),
            (ISSUE_MODULE_CRC_MISMATCH, check.crc_ok is False),
        ):
            issue_id = f"{key}_{check.address.lower()}"
            if active:
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=key,
                    translation_placeholders={
                        "address": check.address,
                        "description": check.description,
                    },
                )
            elif check.error is None:
                ir.async_delete_issue(self._hass, DOMAIN, issue_id)

    async def async_verify_modules(
        self, addresses: list[str] | None = None, *, image: bool = True
    ) -> dict[str, Any]:
        """Check every output module (or ``addresses``): status and CRC."""
        self._acquire()
        api = self._api()
        wanted = {a.upper() for a in addresses} if addresses else None
        self._set_running(True)
        try:
            async with self._lock:
                for address, module_type, description in self.output_modules():
                    if wanted is not None and address not in wanted:
                        continue
                    check, _ = await self._check_module(
                        api, address, module_type, description, image=image
                    )
                    self.checks[address] = check
                    self._apply_issues(check)
                self.last_check_at = dt_util.now()
        finally:
            self._set_running(False)
            self._coordinator.async_update_listeners()
        return self.check_report()

    def check_report(self) -> dict[str, Any]:
        return {
            "health": self.health,
            "checked_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "modules": {addr: check.as_dict() for addr, check in self.checks.items()},
        }

    # -- backup --------------------------------------------------------------

    async def async_backup_modules(self, addresses: list[str] | None = None) -> dict[str, Any]:
        """Read every output module's programming image into a backup folder.

        Writes ``<config>/nikobus_backup/<timestamp>/<address>_<type>.nkm``
        (the raw image) plus ``summary.json`` with the status / CRC result
        of each module. The check results are kept like a verify run.
        """
        self._acquire()
        api = self._api()
        wanted = {a.upper() for a in addresses} if addresses else None
        stamp = dt_util.now().strftime("%Y%m%d-%H%M%S")
        folder = Path(self._hass.config.path(BACKUP_DIR, stamp))
        self._set_running(True)
        try:
            async with self._lock:
                images: dict[str, bytes] = {}
                for address, module_type, description in self.output_modules():
                    if wanted is not None and address not in wanted:
                        continue
                    check, data = await self._check_module(
                        api, address, module_type, description, image=True
                    )
                    self.checks[address] = check
                    self._apply_issues(check)
                    if data is not None:
                        images[f"{address}_{module_type}.nkm"] = data
                self.last_check_at = dt_util.now()
                summary = self.check_report()
                await self._hass.async_add_executor_job(_write_backup, folder, images, summary)
                self.last_backup_path = str(folder)
                self.last_backup_at = self.last_check_at
        finally:
            self._set_running(False)
            self._coordinator.async_update_listeners()
        _LOGGER.info("Nikobus programming backup written to %s (%d image(s))", folder, len(images))
        return {"path": str(folder), "images": sorted(images), **summary}


    # -- raw block reads (diagnostics) ---------------------------------------

    async def async_read_blocks(
        self,
        address: str,
        blocks: list[int],
        *,
        block_size: int = 16,
        link_mode: bool = False,
    ) -> dict[str, Any]:
        """Read raw memory blocks from one module and return them as hex.

        Diagnostic helper for module types whose memory access is not
        settled yet: sends the 16-byte (0x10) or 8-byte (0x22) read for
        each block index, optionally inside link mode (0x18 / 0x19,
        left again in every case). A block that goes unanswered is
        reported as ``"timeout"`` instead of aborting the run.
        """
        self._acquire()
        api = self._api()
        address = address.upper()
        func = FUNC_READ_BLOCK8 if block_size == 8 else FUNC_READ_BLOCK16
        results: dict[str, str] = {}
        self._set_running(True)
        try:
            async with self._lock:
                if link_mode:
                    await api.set_link_mode(address, True)
                try:
                    for block in blocks:
                        index = _block_index(block)
                        key = f"0x{index:04X}"
                        try:
                            # Diagnostic access to the command layer's generic query.
                            payload = await api._command_handler.query(
                                func, address, make_block_index_args(index)
                            )
                        except NikobusError as err:
                            results[key] = f"timeout: {err}" if "ACK" in str(err) else f"error: {err}"
                            continue
                        results[key] = payload[2:].hex().upper() if len(payload) > 2 else payload.hex().upper()
                finally:
                    if link_mode:
                        await api.set_link_mode(address, False)
        finally:
            self._set_running(False)
            self._coordinator.async_update_listeners()
        _LOGGER.info("Block read of %s (%d-byte, link mode %s): %s", address, block_size, link_mode, results)
        return {
            "address": address,
            "block_size": block_size,
            "link_mode": link_mode,
            "blocks": results,
        }

    # -- feedback module LED links -------------------------------------------

    def feedback_modules(self) -> list[tuple[str, str]]:
        """``(address, description)`` of every feedback module (05-207)."""
        return [
            (address, str(entry.get("description") or address))
            for address, module_type, entry in self._modules()
            if module_type == LED_SOURCE_FEEDBACK
        ]

    async def async_import_feedback_leds(self, *, overwrite: bool = False) -> dict[str, Any]:
        """Fill the channels' LED trigger addresses from the feedback module.

        Reads the feedback module's programming, resolves every LED slot
        to the wall key that carries it and writes that key's bus
        address as ``led_on`` / ``led_off`` of each output the LED
        tracks. Values typed by the user stay unless ``overwrite`` is
        set; values from an earlier import are always refreshed.
        """
        self._acquire()
        api = self._api()
        modules = self.feedback_modules()
        if not modules:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_feedback_module"
            )
        self._set_running(True)
        try:
            async with self._lock:
                report: dict[str, Any] = {
                    "feedback_modules": [address for address, _ in modules],
                    "leds": 0,
                    "resolved": 0,
                    "channels_updated": 0,
                    "channels_kept": 0,
                    "unresolved": [],
                    "assignments": [],
                }
                for address, _description in modules:
                    image = await api.read_module_memory(address, LED_SOURCE_FEEDBACK)
                    decoded = decode_feedback_image(image)
                    self._apply_feedback_leds(decoded, report, overwrite=overwrite)
                if report["channels_updated"]:
                    await self._coordinator.async_on_module_save()
        finally:
            self._set_running(False)
            self._coordinator.async_update_listeners()
        _LOGGER.info(
            "Feedback LED import: %d LED(s), %d resolved, %d channel(s) updated, %d kept",
            report["leds"], report["resolved"], report["channels_updated"], report["channels_kept"],
        )
        return report

    def _apply_feedback_leds(
        self, decoded: FeedbackImage, report: dict[str, Any], *, overwrite: bool
    ) -> None:
        buttons = (self._coordinator.dict_button_data or {}).get("nikobus_button") or {}
        for led in decoded.leds:
            tracked = [
                (item.output.module_address.upper(), item.output.channel)
                for item in led.outputs
                if item.output is not None
            ]
            if not tracked:
                continue
            report["leds"] += 1
            resolved = resolve_feedback_led(led, tracked, buttons)
            if resolved is None:
                report["unresolved"].append(
                    {"slot": led.slot, "plates": list(led.plate_addresses), "outputs": tracked}
                )
                continue
            plate, key_label, bus_address = resolved
            report["resolved"] += 1
            for module_address, channel in tracked:
                outcome = self._set_channel_led(module_address, channel, bus_address, overwrite)
                if outcome is None:
                    continue
                report["channels_updated" if outcome else "channels_kept"] += 1
                report["assignments"].append(
                    {
                        "module_address": module_address,
                        "channel": channel,
                        "plate": plate,
                        "key": key_label,
                        "bus_address": bus_address,
                        "updated": outcome,
                    }
                )

    def _set_channel_led(
        self, module_address: str, channel: int, bus_address: str, overwrite: bool
    ) -> bool | None:
        """Write ``led_on``/``led_off`` on one channel.

        Returns ``True`` when written, ``False`` when an existing value was
        kept, ``None`` when the module or channel is unknown.
        """
        hit = find_module(self._coordinator.module_storage.data, module_address)
        if hit is None:
            return None
        channels = hit[1].get("channels")
        if not isinstance(channels, list) or not 1 <= channel <= len(channels):
            return None
        entry = channels[channel - 1]
        if not isinstance(entry, dict):
            return None
        existing = entry.get("led_on") or entry.get("led_off")
        imported = entry.get("led_source") == LED_SOURCE_FEEDBACK
        if existing and not (overwrite or imported):
            return False
        if existing == bus_address and entry.get("led_off") == bus_address and imported:
            return False
        entry["led_on"] = bus_address
        entry["led_off"] = bus_address
        entry["led_source"] = LED_SOURCE_FEEDBACK
        return True


def _block_index(value: Any) -> int:
    """Block index from an int or a hex string (``"600"`` / ``"0x600"``)."""
    if isinstance(value, int):
        return value
    text = str(value).strip().lower().removeprefix("0x")
    return int(text, 16)


def resolve_feedback_led(
    led: FeedbackLed,
    tracked: list[tuple[str, int]],
    buttons: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Find the wall key behind an LED slot: ``(plate, key_label, bus_address)``.

    The plate is the group's module address (either bit-0 variant that
    exists in the button store). Among its keys, the one whose links
    drive an output the LED tracks is the LED's key — a feedback LED
    normally sits on the key that switches the light. When the links
    do not single one out, the slot's row inside the group decides.
    """
    plate_entry: dict[str, Any] | None = None
    plate = ""
    for candidate in led.plate_addresses:
        entry = buttons.get(candidate) or buttons.get(candidate.lower())
        if isinstance(entry, dict):
            plate_entry, plate = entry, candidate
            break
    if plate_entry is None:
        return None
    op_points = plate_entry.get("operation_points")
    if not isinstance(op_points, dict):
        return None
    keys: dict[str, str] = {}
    matching: list[str] = []
    for key_label, op_point in op_points.items():
        if not isinstance(op_point, dict) or str(key_label).startswith("IR:"):
            continue
        bus_address = str(op_point.get("bus_address") or "").upper()
        if not bus_address:
            continue
        keys[str(key_label)] = bus_address
        links = {(addr, chan) for addr, chan, _ in iter_link_outputs(op_point)}
        if any(target in links for target in tracked):
            matching.append(str(key_label))
    if not keys:
        return None
    guess: str | None = None
    rows = _LED_ROW_KEYS.get(len(keys))
    if rows is not None and led.row is not None and led.row < len(rows):
        guess = rows[led.row]
    if len(matching) == 1:
        chosen = matching[0]
    elif matching:
        chosen = guess if guess in matching else matching[0]
    elif guess in keys:
        chosen = guess
    else:
        return None
    return plate, chosen, keys[chosen]


def _write_backup(folder: Path, images: dict[str, bytes], summary: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in images.items():
        (folder / name).write_bytes(data)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))
