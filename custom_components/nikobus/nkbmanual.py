"""Manual-configuration loader for installs auto-discovery cannot crack.

Some PC-Link generations (pre-Gen3 / Gen2) store button-link data in a
format the Gen3-calibrated decoder in ``nikobus-connect`` does not
parse — see the 0.5.24 CHANGELOG section "What this does NOT fix".
For those installs the user enables ``manual_config`` in the config
flow; the integration then treats the legacy v1 files
(``nikobus_module_config.json`` / ``nikobus_button_config.json``) as
the **source of truth** and re-applies them into the v2 stores on
every startup.

Pre-v2 semantics: the files are fully declarative. Whatever lives in
them is what HA exposes after the next reload — additions, edits,
renames, removals. There is no merge with options-flow state; users
who want to customise a channel's entity type, LED triggers, travel
times, or description do so by editing the file. The options-flow
"Customise a module" path is hidden in manual mode so it does not
present a UI whose changes get wiped on the next reload.

Two practical notes:

  * No rename. The source files stay put across reloads.
  * The button file is consumed for routing data, not just for
    user-facing names. Each v1 entry becomes a single-key physical
    button whose ``operation_points["1A"]`` carries the v1 bus
    address — the v2 router matches bus addresses, so single-key
    shape is enough to route presses to HA entities.

If the user already upgraded to v2 once before flipping the toggle,
the v1 file will have been renamed to ``<name>.migrated`` by the
one-shot migration. This loader falls back to the ``.migrated``
suffix so the toggle works without manual file renaming.
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
from .nkbmigration import convert_legacy_to_flat
from .nkbstorage import NikobusModuleStorage

_LOGGER = logging.getLogger(__name__)

_MIGRATED_SUFFIX = ".migrated"


async def _read_json_with_fallback(
    hass: HomeAssistant, filename: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(path, payload)`` for the first existing variant of ``filename``.

    Tries the canonical name first. If only the ``.migrated`` variant
    (left behind by the one-shot v1→v2 migration) exists, rename it
    back to the canonical name so the user edits a file with the
    expected filename — and so future reads hit the fast path. If a
    canonical file already exists, ``.migrated`` is left untouched
    (the canonical one is whatever the user put there). Returns
    ``(None, None)`` when neither exists or the chosen file is
    unreadable.
    """
    canonical_path = hass.config.path(filename)
    migrated_path = canonical_path + _MIGRATED_SUFFIX

    canonical_exists = await hass.async_add_executor_job(
        os.path.isfile, canonical_path
    )

    if not canonical_exists:
        migrated_exists = await hass.async_add_executor_job(
            os.path.isfile, migrated_path
        )
        if migrated_exists:
            try:
                await hass.async_add_executor_job(
                    os.rename, migrated_path, canonical_path
                )
                _LOGGER.info(
                    "Manual-config: renamed %s back to %s so the file "
                    "is editable under its canonical name.",
                    migrated_path,
                    canonical_path,
                )
                canonical_exists = True
            except OSError as err:
                _LOGGER.warning(
                    "Manual-config: could not rename %s back to %s "
                    "(%s); reading from .migrated and leaving it in "
                    "place.",
                    migrated_path,
                    canonical_path,
                    err,
                )

    for path in (canonical_path, migrated_path):
        if not await hass.async_add_executor_job(os.path.isfile, path):
            continue
        try:
            async with aio_open(path, mode="r") as fh:
                raw = await fh.read()
            return path, json.loads(raw)
        except (OSError, json.JSONDecodeError) as err:
            _LOGGER.warning(
                "Manual-config: %s is unreadable (%s); skipping.", path, err
            )
            return None, None
    return None, None


def _tag_manual_source(entry: dict[str, Any]) -> None:
    """Mark a module entry as manual-config-sourced for diagnostics."""
    discovered_info = entry.get("discovered_info")
    if not isinstance(discovered_info, dict):
        discovered_info = {}
    discovered_info["source"] = "manual_config"
    entry["discovered_info"] = discovered_info


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
    path, payload = await _read_json_with_fallback(
        hass, MANUAL_MODULE_CONFIG_FILENAME
    )
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


def _single_key_fallback(bus_address: str, description: str) -> dict[str, Any]:
    """Build a 1-channel synthetic record for an unlinked v1 entry.

    Used for IR/virtual/scene entries that have no ``linked_button``
    block. The v2 router routes by ``operation_points[*].bus_address``,
    so a single op-point on a synthetic physical record (keyed by the
    bus address itself) is enough to make those entries fire HA
    events the way they did in v1.
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
    path, payload = await _read_json_with_fallback(
        hass, MANUAL_BUTTON_CONFIG_FILENAME
    )
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
