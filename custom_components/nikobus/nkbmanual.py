"""Step-1 inventory loader for installs without a PC-Link module.

Some users connect to the Nikobus over a Feedback module instead of a
PC-Link. There's no `#A` broadcast partner on the bus, so the normal
discovery's step 1 (PC-Link inventory) has no source. Instead, the
integration imports the user-authored ``nikobus_module_config.json``
and ``nikobus_button_config.json`` as the step-1 inventory.

After import, **step 2** (the per-module register scan) populates the
link-records (``linked_modules`` / ``outputs[]``) just as it does after
a PC-Link inventory. The import deliberately does **not** carry over
the ``linked_modules`` block from the file — that's step-2 territory
and would conflict with a fresh scan.

Lifecycle:

  * Files stay in place under their canonical names. No rename, no
    "consumed" marker. Re-import is implicit on every coordinator
    setup — the files ARE the inventory source.
  * Files are fully declarative: anything not in the file is removed
    from the corresponding store. Users edit the file when they
    install / remove / rename a module or button.
  * After import, run "Scan all modules" to populate link records.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from aiofiles import open as aio_open
from homeassistant.core import HomeAssistant

from .const import (
    MANUAL_BUTTON_CONFIG_FILENAME,
    MANUAL_MODULE_CONFIG_FILENAME,
)
from .nkbstorage import NikobusModuleStorage

_LOGGER = logging.getLogger(__name__)


async def _read_json(
    hass: HomeAssistant, filename: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(path, payload)`` if ``filename`` exists and is parseable.

    Returns ``(None, None)`` when the file doesn't exist or can't be
    read. Only the canonical filename is checked — pre-2.11.4 ``.migrated``
    fallback was removed; users with leftover ``.migrated`` files from
    earlier versions should rename them back to the canonical name.
    """
    path = hass.config.path(filename)
    if not await hass.async_add_executor_job(os.path.isfile, path):
        return None, None
    try:
        async with aio_open(path, mode="r") as fh:
            raw = await fh.read()
        return path, json.loads(raw)
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning(
            "Manual-config: %s is unreadable (%s); skipping.", path, err
        )
        return None, None


def _tag_manual_source(entry: dict[str, Any]) -> None:
    """Mark a module entry as manual-config-sourced for diagnostics."""
    discovered_info = entry.get("discovered_info")
    if not isinstance(discovered_info, dict):
        discovered_info = {}
    discovered_info["source"] = "manual_config"
    entry["discovered_info"] = discovered_info


# ---------------------------------------------------------------------------
# v1 (legacy nested) → flat-by-address conversion
# ---------------------------------------------------------------------------
#
# The v1 file shape is ``{module_type: [entries | {addr: entry}]}``. The
# 0.4.0 module store keeps a single dict keyed by address, with
# ``module_type`` carried on each entry. These helpers translate the v1
# shape into the flat shape — the only consumer is ``_apply_module_config``
# below.


def convert_legacy_to_flat(legacy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert ``{module_type: [entries | {addr: entry}]}`` to ``{addr: entry}``.

    User fields on each channel are preserved verbatim. Roller channels
    that used the legacy single ``operation_time`` key are split into
    ``operation_time_up`` / ``operation_time_down``. Every entry is
    tagged with its ``module_type`` so downstream code can still group
    by hardware class without keeping a parallel nested dict.
    """
    flat: dict[str, dict[str, Any]] = {}
    if not isinstance(legacy, dict):
        return flat

    for module_type, modules in legacy.items():
        if not isinstance(module_type, str):
            continue

        iter_entries: list[tuple[str | None, dict[str, Any]]] = []
        if isinstance(modules, list):
            for entry in modules:
                if isinstance(entry, dict):
                    iter_entries.append((entry.get("address"), entry))
        elif isinstance(modules, dict):
            for addr, entry in modules.items():
                if isinstance(entry, dict):
                    iter_entries.append((entry.get("address") or addr, entry))
        else:
            _LOGGER.warning(
                "Skipping module group %s with unsupported type %s",
                module_type,
                type(modules).__name__,
            )
            continue

        for address, entry in iter_entries:
            if not isinstance(address, str) or not address:
                continue
            key = address.upper()
            flat[key] = _convert_entry(key, module_type, entry)

    return flat


def _convert_entry(
    address: str, module_type: str, entry: dict[str, Any]
) -> dict[str, Any]:
    """Convert a single legacy module entry to the flat shape."""
    channels_in = entry.get("channels", [])
    channels_out: list[dict[str, Any]] = []
    if isinstance(channels_in, list):
        for ch in channels_in:
            if isinstance(ch, dict):
                channels_out.append(_convert_channel(module_type, ch))
            elif isinstance(ch, str):
                # Very old shapes stored channel descriptions as bare strings.
                channels_out.append({"description": ch})

    out: dict[str, Any] = {
        "module_type": module_type,
        "description": entry.get("description") or f"Module {address}",
        "model": entry.get("model") or "",
        "channels": channels_out,
    }

    # Carry discovered_info through verbatim when it already exists.
    if isinstance(entry.get("discovered_info"), dict):
        out["discovered_info"] = dict(entry["discovered_info"])

    return out


def _convert_channel(
    module_type: str, channel: dict[str, Any]
) -> dict[str, Any]:
    """Convert a single legacy channel, splitting ``operation_time`` for rollers."""
    out: dict[str, Any] = {
        "description": channel.get("description", ""),
    }

    # Optional fields — only emitted when set in the source, so we don't
    # invent defaults that the options flow would treat as user edits.
    for key in ("entity_type", "led_on", "led_off"):
        if key in channel and channel[key] not in ("", None):
            out[key] = channel[key]

    if module_type == "roller_module":
        if "operation_time_up" in channel:
            out["operation_time_up"] = channel["operation_time_up"]
        if "operation_time_down" in channel:
            out["operation_time_down"] = channel["operation_time_down"]
        # Legacy single ``operation_time`` → split into both directions.
        if "operation_time" in channel and "operation_time_up" not in out:
            legacy_time = channel["operation_time"]
            out["operation_time_up"] = legacy_time
            out.setdefault("operation_time_down", legacy_time)

    return out


async def _apply_module_config(
    hass: HomeAssistant, store: NikobusModuleStorage
) -> tuple[int, str | None]:
    """Replace the module store with ``nikobus_module_config.json``.

    Fully declarative: anything not in the file is removed from the
    store. Returns ``(modules_in_file, source_path)`` — ``(0, None)``
    when no file is found, ``(0, path)`` when the file parsed but
    contained no convertible entries (in which case the caller should
    decide whether to wipe the store anyway).
    """
    path, payload = await _read_json(hass, MANUAL_MODULE_CONFIG_FILENAME)
    if path is None or not isinstance(payload, dict):
        return 0, None

    flat = convert_legacy_to_flat(payload)
    for entry in flat.values():
        _tag_manual_source(entry)

    # Wholesale replacement. Removing an entry from the file removes
    # it from the store on the next reload — pre-v2 declarative
    # semantics. Options-flow edits do not survive (the file is the
    # truth).
    store.data["nikobus_module"] = flat
    return len(flat), path


def _extract_physical_info(
    entry: dict[str, Any],
) -> tuple[str, str, int, str, str] | None:
    """Pull the physical-button identity out of a v1 button entry.

    The v1 layout is per-operation-point: each list entry is one key
    press, and entries that share ``linked_button[0].address`` are
    different keys of the same physical button. Returns
    ``(physical_addr, key_label, channels, type, model)`` when the
    entry carries that data, else ``None`` for entries that don't
    belong to a real bus device (IR-only / scene-trigger / virtual /
    auto-generated ``DISCOVERED -`` placeholders).
    """
    linked = entry.get("linked_button")
    if not isinstance(linked, list) or not linked:
        return None
    info = linked[0]
    if not isinstance(info, dict):
        return None
    phys = str(info.get("address") or "").strip().upper()
    key = str(info.get("key") or "").strip().upper()
    channels = info.get("channels")
    if not phys or not key or not isinstance(channels, int):
        return None
    type_str = str(info.get("type") or "").strip() or "Button"
    model = str(info.get("model") or "").strip()
    return phys, key, channels, type_str, model


def _new_physical_record(
    physical_addr: str, type_str: str, model: str, channels: int
) -> dict[str, Any]:
    return {
        "type": type_str,
        "model": model,
        "channels": channels,
        "description": f"{type_str} ({physical_addr})",
        "operation_points": {},
    }


def _single_key_fallback(
    bus_address: str,
    description: str,
) -> dict[str, Any]:
    """Build a 1-channel synthetic record for an entry without a
    ``linked_button`` block (IR-only / virtual / scene placeholder).

    The router matches by ``operation_points[*].bus_address``, so a
    single op-point on a synthetic physical record (keyed by the bus
    address itself) is enough to make these entries fire HA events.
    ``linked_modules`` is left empty — step 2 (per-module register
    scan) populates real link records when the underlying button has
    any.
    """
    label = description or f"#N{bus_address}"
    return {
        "type": "Manual button",
        "model": "",
        "channels": 1,
        "description": label,
        "operation_points": {
            "1A": {
                "bus_address": bus_address,
                "description": label,
                "linked_modules": [],
            }
        },
    }


async def _apply_button_config(
    hass: HomeAssistant, button_data: dict[str, Any]
) -> tuple[int, int, str | None]:
    """Replace the button store with ``nikobus_button_config.json``.

    Fully declarative: ``button_data["nikobus_button"]`` is wiped and
    rebuilt from the file. ``button_data`` is the live reference the
    coordinator passes (same object as ``button_storage.data``), so a
    subsequent ``async_save`` persists the result.

    Returns ``(physical_buttons, total_op_points, source_path)``.
    """
    path, payload = await _read_json(hass, MANUAL_BUTTON_CONFIG_FILENAME)
    if path is None or not isinstance(payload, dict):
        return 0, 0, None

    raw = payload.get("nikobus_button")
    # v1 files store a list of entries; the v1 NikobusConfig loader
    # converted them to a dict keyed by address at load time. Accept
    # both shapes so users with either snapshot work without manual
    # conversion.
    legacy_iter: list[dict[str, Any]] = []
    if isinstance(raw, list):
        legacy_iter = [e for e in raw if isinstance(e, dict)]
    elif isinstance(raw, dict):
        for addr, entry in raw.items():
            if isinstance(entry, dict):
                merged = dict(entry)
                merged.setdefault("address", addr)
                legacy_iter.append(merged)

    new_buttons: dict[str, Any] = {}
    op_points_total = 0

    for entry in legacy_iter:
        bus_address = str(entry.get("address") or "").strip().upper()
        if not bus_address:
            continue
        description = str(entry.get("description") or "").strip()
        # ``linked_modules`` is NOT imported from the manual file
        # (2.11.4+). The file is a step-1 inventory source — it tells
        # us which physical buttons exist and which keys they have.
        # The link records (``linked_modules[].outputs[]``) are
        # step-2 territory; "Scan all modules" populates them from
        # the per-module register tables on the bus. Importing them
        # from the file would create stale duplicates that step 2
        # would have to dedup. Cleaner to start from empty and let
        # step 2 fill it.

        physical = _extract_physical_info(entry)
        if physical is not None:
            phys_addr, key_label, channels, type_str, model = physical
            phys_entry = new_buttons.get(phys_addr)
            if phys_entry is None:
                phys_entry = _new_physical_record(
                    phys_addr, type_str, model, channels
                )
                new_buttons[phys_addr] = phys_entry
            # Op-point conflict (two entries claiming same key on the
            # same physical) — last entry wins. The file is the truth.
            phys_entry["operation_points"][key_label] = {
                "bus_address": bus_address,
                "description": description
                or f"Push button {key_label} #N{bus_address}",
                "linked_modules": [],
            }
            op_points_total += 1
        else:
            # Unlinked entry — IR/virtual/scene/placeholder. Don't
            # clobber a properly-grouped physical button that happens
            # to use this bus address as its physical key.
            if bus_address in new_buttons:
                continue
            new_buttons[bus_address] = _single_key_fallback(
                bus_address, description
            )
            op_points_total += 1

    button_data["nikobus_button"] = new_buttons
    return len(new_buttons), op_points_total, path


async def async_apply_manual_config(
    hass: HomeAssistant,
    module_store: NikobusModuleStorage,
    button_data: dict[str, Any],
) -> bool:
    """Apply the v1 module + button config files into the v2 stores.

    Manual-config mode is fully declarative: the files are the source
    of truth, and the stores are rewritten to match on every call.
    Removing an entry from the file removes the corresponding entity
    on the next reload.

    Returns ``True`` if at least one of the files was present and
    parseable (so the coordinator should persist the result). Errors
    on either file are logged and swallowed so the integration still
    comes up.
    """
    try:
        modules_loaded, module_path = await _apply_module_config(hass, module_store)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Manual-config: error applying %s; module store left unchanged",
            MANUAL_MODULE_CONFIG_FILENAME,
        )
        modules_loaded = 0
        module_path = None

    try:
        buttons_loaded, op_points_total, button_path = await _apply_button_config(
            hass, button_data
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Manual-config: error applying %s; button store left unchanged",
            MANUAL_BUTTON_CONFIG_FILENAME,
        )
        buttons_loaded = 0
        op_points_total = 0
        button_path = None

    if module_path is None and button_path is None:
        _LOGGER.warning(
            "Manual-config enabled but neither %s nor %s was found in %s. "
            "Place at least one file there and reload the integration.",
            MANUAL_MODULE_CONFIG_FILENAME,
            MANUAL_BUTTON_CONFIG_FILENAME,
            hass.config.path(""),
        )
        return False

    _LOGGER.info(
        "Manual-config applied (declarative): modules=%d (source=%s) "
        "buttons=%d physical / %d operation-points (source=%s). "
        "Reload the integration after editing either file to apply "
        "changes.",
        modules_loaded,
        module_path or "—",
        buttons_loaded,
        op_points_total,
        button_path or "—",
    )
    return True
