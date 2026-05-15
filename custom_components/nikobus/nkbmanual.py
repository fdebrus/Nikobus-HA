"""Manual-configuration loader for installs auto-discovery cannot crack.

Some PC-Link generations (pre-Gen3 / Gen2) store button-link data in a
format the Gen3-calibrated decoder in ``nikobus-connect`` does not parse
— see the 0.5.24 CHANGELOG section "What this does NOT fix". For those
installs the user enables ``manual_config`` in the config flow; the
integration then treats the legacy v1 files
(``nikobus_module_config.json`` / ``nikobus_button_config.json``) as
the source of truth and re-applies them into the v2 stores on every
startup.

Two key differences from the one-shot migration in ``nkbmigration.py``:

  * No rename. The source files stay put so they survive across
    restarts (and remain editable by the user).
  * The button file is consumed for routing data, not just for
    user-facing names. Each v1 entry becomes a single-key physical
    button whose ``operation_points["1A"]`` carries the v1 bus address
    — the router matches bus addresses, so single-key shape is enough
    to route presses to HA entities.

If the user already upgraded to v2 once before flipping the toggle, the
v1 file will have been renamed to ``<name>.migrated`` by the one-shot
migration. This loader falls back to the ``.migrated`` suffix so the
toggle works without manual file renaming.
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

# Fields the user can edit via the options flow that must survive a
# re-apply on top of what the YAML defines. Mirrors
# ``nkbmigration._USER_CHANNEL_FIELDS`` plus a couple of extras the
# library can also write back during a successful auto-discovery sweep
# (which manual-mode users may still run by hand from the device button).
_PRESERVED_CHANNEL_FIELDS: tuple[str, ...] = (
    "entity_type",
    "led_on",
    "led_off",
    "operation_time_up",
    "operation_time_down",
)


async def _read_json_with_fallback(
    hass: HomeAssistant, filename: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(path, payload)`` for the first existing variant of ``filename``.

    Tries the canonical name first, then the ``.migrated`` suffix that
    the one-shot v1→v2 migration leaves behind. Returns ``(None, None)``
    when neither exists or both are unreadable.
    """
    candidates = [
        hass.config.path(filename),
        hass.config.path(filename + _MIGRATED_SUFFIX),
    ]
    for path in candidates:
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


def _merge_manual_module(
    store_entry: dict[str, Any] | None, yaml_entry: dict[str, Any]
) -> dict[str, Any]:
    """Deep-merge a YAML module entry onto an existing Store entry.

    YAML wins for every field it sets at the module level. Per channel,
    YAML fields override but options-flow-only fields
    (``entity_type``, ``led_on/off``, ``operation_time_*``) are
    preserved when the YAML doesn't carry them — so a user can refine a
    YAML-declared module via the options flow without having those
    edits reverted on the next startup.
    """
    if not isinstance(store_entry, dict):
        store_entry = {}

    merged = dict(store_entry)
    for key, value in yaml_entry.items():
        if key == "channels":
            continue
        merged[key] = value

    yaml_channels = yaml_entry.get("channels") or []
    store_channels = store_entry.get("channels") or []
    if not isinstance(yaml_channels, list):
        yaml_channels = []
    if not isinstance(store_channels, list):
        store_channels = []

    out_channels: list[dict[str, Any]] = []
    for idx, yaml_ch in enumerate(yaml_channels):
        store_ch = (
            store_channels[idx] if idx < len(store_channels) else {}
        )
        if not isinstance(store_ch, dict):
            store_ch = {}
        if not isinstance(yaml_ch, dict):
            out_channels.append(store_ch)
            continue
        combined = dict(yaml_ch)
        for field in _PRESERVED_CHANNEL_FIELDS:
            if field not in combined and field in store_ch:
                combined[field] = store_ch[field]
        out_channels.append(combined)

    # Channels beyond the YAML's length are preserved verbatim — the
    # user may have run a successful auto-discovery sweep that added
    # rows the YAML hasn't been updated for yet.
    if len(store_channels) > len(yaml_channels):
        out_channels.extend(
            ch if isinstance(ch, dict) else {} for ch in store_channels[len(yaml_channels):]
        )
    merged["channels"] = out_channels

    # Manual mode does not own a device_type byte; surface a marker on
    # ``discovered_info`` so diagnostics can tell apart YAML-sourced
    # rows from auto-discovered ones.
    discovered_info = merged.get("discovered_info")
    if not isinstance(discovered_info, dict):
        discovered_info = {}
    discovered_info["source"] = "manual_config"
    merged["discovered_info"] = discovered_info
    return merged


async def _apply_module_config(
    hass: HomeAssistant, store: NikobusModuleStorage
) -> tuple[int, int, str | None]:
    """Apply ``nikobus_module_config.json`` into the module store.

    Returns ``(modules_applied, modules_added, source_path)``.
    """
    path, payload = await _read_json_with_fallback(
        hass, MANUAL_MODULE_CONFIG_FILENAME
    )
    if path is None or not isinstance(payload, dict):
        return 0, 0, None

    flat = convert_legacy_to_flat(payload)
    if not flat:
        return 0, 0, path

    modules = store.data.setdefault("nikobus_module", {})
    if not isinstance(modules, dict):
        modules = {}
        store.data["nikobus_module"] = modules

    added = 0
    for address, yaml_entry in flat.items():
        addr_upper = str(address).upper()
        existing = modules.get(addr_upper)
        if existing is None:
            added += 1
        modules[addr_upper] = _merge_manual_module(existing, yaml_entry)

    return len(flat), added, path


def _build_button_entry(
    bus_address: str,
    description: str,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a v2 button record from a single v1 entry.

    Each v1 entry becomes a single-key physical button (channels=1) so
    the v2 router's bus-address lookup finds it. ``bus_address`` is
    used as both the dict key and the ``operation_points["1A"]``
    address — the router only cares about the latter for routing, but
    keying by it gives a stable, collision-free physical identity.
    """
    op_desc = description or f"#N{bus_address}"
    phys_desc = description or f"#N{bus_address}"

    entry: dict[str, Any] = dict(existing or {})
    entry["type"] = entry.get("type") or "Manual button"
    entry.setdefault("model", "")
    entry["channels"] = 1
    # Don't clobber a description the user may have set via HA's device
    # registry rename machinery — but do replace the auto-generated
    # ``#NXXXXXX`` placeholder when the YAML now carries a real label.
    current_desc = entry.get("description")
    if not current_desc or current_desc.endswith(f"#N{bus_address}"):
        entry["description"] = phys_desc

    op_points = entry.setdefault("operation_points", {})
    if not isinstance(op_points, dict):
        op_points = {}
        entry["operation_points"] = op_points

    op_point = op_points.setdefault("1A", {})
    if not isinstance(op_point, dict):
        op_point = {}
        op_points["1A"] = op_point
    op_point["bus_address"] = bus_address
    current_op_desc = op_point.get("description")
    if not current_op_desc or current_op_desc.endswith(f"#N{bus_address}"):
        op_point["description"] = op_desc
    op_point.setdefault("linked_modules", [])
    # Drop any other op-points the auto-discovery sweep may have
    # populated for this physical record — manual mode owns the shape
    # of buttons it declares, and a stray op-point would cause the
    # button platform to emit phantom entities.
    for stale_key in [k for k in op_points if k != "1A"]:
        op_points.pop(stale_key, None)
    return entry


async def _apply_button_config(
    hass: HomeAssistant, button_data: dict[str, Any]
) -> tuple[int, int, str | None]:
    """Apply ``nikobus_button_config.json`` into the button store.

    Returns ``(buttons_applied, buttons_added, source_path)``. Mutates
    ``button_data`` in place — the coordinator passes the live
    ``button_storage.data`` reference, so a subsequent ``async_save``
    picks up these changes.
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
    if not legacy_iter:
        return 0, 0, path

    buttons = button_data.setdefault("nikobus_button", {})
    if not isinstance(buttons, dict):
        buttons = {}
        button_data["nikobus_button"] = buttons

    added = 0
    applied = 0
    for entry in legacy_iter:
        address = str(entry.get("address") or "").strip().upper()
        if not address:
            continue
        description = str(entry.get("description") or "").strip()
        existing = buttons.get(address)
        if existing is None:
            added += 1
        buttons[address] = _build_button_entry(address, description, existing)
        applied += 1

    return applied, added, path


async def async_apply_manual_config(
    hass: HomeAssistant,
    module_store: NikobusModuleStorage,
    button_data: dict[str, Any],
) -> bool:
    """Apply the v1 module + button config files into the v2 stores.

    Returns ``True`` if any change was made (and a save is therefore
    warranted), ``False`` otherwise. Errors on either file are logged
    and swallowed — the integration must still come up so the user can
    review the logs and fix the file.
    """
    try:
        modules_applied, modules_added, module_path = await _apply_module_config(
            hass, module_store
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Manual-config: error applying %s; continuing without module overlay",
            MANUAL_MODULE_CONFIG_FILENAME,
        )
        modules_applied = modules_added = 0
        module_path = None

    try:
        buttons_applied, buttons_added, button_path = await _apply_button_config(
            hass, button_data
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Manual-config: error applying %s; continuing without button overlay",
            MANUAL_BUTTON_CONFIG_FILENAME,
        )
        buttons_applied = buttons_added = 0
        button_path = None

    if not (modules_applied or buttons_applied):
        _LOGGER.info(
            "Manual-config: neither %s nor %s yielded entries; "
            "auto-discovery (if it works for this hardware) remains available "
            "via the device button.",
            MANUAL_MODULE_CONFIG_FILENAME,
            MANUAL_BUTTON_CONFIG_FILENAME,
        )
        return False

    _LOGGER.info(
        "Manual-config applied: modules=%d (new=%d, source=%s) "
        "buttons=%d (new=%d, source=%s)",
        modules_applied,
        modules_added,
        module_path or "—",
        buttons_applied,
        buttons_added,
        button_path or "—",
    )
    return True
