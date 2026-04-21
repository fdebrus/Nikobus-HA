"""One-shot migration from the legacy ``nikobus_module_config.json`` file
to the ``.storage/nikobus.modules`` Store introduced with nikobus-connect 0.4.0.

The migration only runs when the Store is empty. After a successful convert,
the source JSON is renamed to ``<name>.migrated.bak`` — never deleted —
so users retain an escape hatch for one release.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from aiofiles import open as aio_open
from homeassistant.core import HomeAssistant

from .nkbstorage import NikobusModuleStorage

_LOGGER = logging.getLogger(__name__)

LEGACY_FILENAME = "nikobus_module_config.json"
_MIGRATED_SUFFIX = ".migrated.bak"

# Types the legacy file groups modules under. Anything else is kept verbatim
# under its own ``module_type`` bucket on the flat entry.
_OUTPUT_MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")


def convert_legacy_to_flat(legacy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert ``{module_type: [entries | {addr: entry}]}`` to ``{addr: entry}``.

    Produces the inner shape that lives under ``module_data["nikobus_module"]``.
    Caller is responsible for wrapping it in ``{"nikobus_module": ...}`` before
    persisting.

    User fields on each channel are preserved verbatim. Roller channels that
    used the legacy single ``operation_time`` key are split into
    ``operation_time_up`` / ``operation_time_down``. Every entry is tagged
    with its ``module_type`` so downstream code can still group by hardware
    class without keeping a parallel nested dict.
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
                "Skipping module group %s with unsupported type %s during migration",
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
    """Convert a single legacy module entry to the flat 0.4.0 shape."""
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


def _convert_channel(module_type: str, channel: dict[str, Any]) -> dict[str, Any]:
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
        # Legacy: single ``operation_time`` string applied to both directions.
        # 0.4.0: separate up/down values.
        if "operation_time_up" in channel:
            out["operation_time_up"] = channel["operation_time_up"]
        if "operation_time_down" in channel:
            out["operation_time_down"] = channel["operation_time_down"]
        if "operation_time" in channel and "operation_time_up" not in out:
            legacy_time = channel["operation_time"]
            out["operation_time_up"] = legacy_time
            out.setdefault("operation_time_down", legacy_time)

    return out


async def async_migrate_legacy_module_config(
    hass: HomeAssistant, store: NikobusModuleStorage
) -> bool:
    """Run the one-shot migration.

    Returns ``True`` when a migration was performed, ``False`` otherwise
    (store already populated, no legacy file, or file unreadable).

    Side effects on success:
      * ``store.data["nikobus_module"]`` is replaced with the converted entries.
      * ``store.async_save()`` is awaited.
      * The source file is renamed to ``<config>/<LEGACY_FILENAME>.migrated.bak``.
    """
    if not store.is_empty:
        _LOGGER.debug(
            "Module store already populated (%d entries) — skipping legacy migration",
            len(store.data.get("nikobus_module", {})),
        )
        return False

    source_path = hass.config.path(LEGACY_FILENAME)
    if not os.path.exists(source_path):
        _LOGGER.debug(
            "No legacy module config at %s — skipping migration", source_path
        )
        return False

    try:
        async with aio_open(source_path, mode="r") as handle:
            raw = await handle.read()
        legacy = json.loads(raw)
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning(
            "Could not read legacy module config at %s (%s); skipping migration",
            source_path,
            err,
        )
        return False

    flat = convert_legacy_to_flat(legacy)
    if not flat:
        _LOGGER.info(
            "Legacy module config at %s contained no convertible entries — "
            "leaving store empty and renaming the source anyway",
            source_path,
        )

    store.data["nikobus_module"] = flat
    await store.async_save()

    backup_path = source_path + _MIGRATED_SUFFIX
    try:
        os.replace(source_path, backup_path)
    except OSError as err:
        # Rename failure is non-fatal — the data is already in the Store.
        # Log loudly so the user knows the file is still there on disk.
        _LOGGER.warning(
            "Migrated %d modules to the Store, but could not rename %s to %s: %s. "
            "The integration will now ignore the source file; you can rename or "
            "remove it manually.",
            len(flat),
            source_path,
            backup_path,
            err,
        )
        return True

    _LOGGER.info(
        "Migrated %d modules from %s to the .storage/nikobus.modules Store. "
        "Source renamed to %s.",
        len(flat),
        source_path,
        backup_path,
    )
    return True
