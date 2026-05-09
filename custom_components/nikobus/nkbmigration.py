"""One-shot migration from the legacy ``nikobus_module_config.json`` file
to the ``.storage/nikobus.modules`` Store introduced with nikobus-connect 0.4.0.

Two flavours, picked by Store state:

* **Empty Store** — full import. The legacy entries replace the empty
  Store and the source file is renamed to ``.migrated``.

* **Populated Store** — overlay user-editable fields (module / channel
  descriptions, entity_type, LED triggers, roller travel times) onto
  matching Store entries by address + channel index. Modules in the
  legacy file whose addresses aren't in the Store get an INFO log but
  are skipped — the Store is the authority for what exists. The source
  file is still renamed afterwards so it stops being a confusing
  artifact and the rename matches the button-side migration in
  ``__init__.py`` (``nikobus_button_config.json`` → ``.migrated``).
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
_MIGRATED_SUFFIX = ".migrated"

# Types the legacy file groups modules under. Anything else is kept verbatim
# under its own ``module_type`` bucket on the flat entry.
_OUTPUT_MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")

# Fields the user can edit via the options flow. Discovery-owned fields
# (``model``, ``module_type``, ``discovered_info``, channel count) are
# intentionally NOT in this set — overlaying those would let stale legacy
# data clobber what discovery just established.
_USER_MODULE_FIELDS: tuple[str, ...] = ("description",)
_USER_CHANNEL_FIELDS: tuple[str, ...] = (
    "description",
    "entity_type",
    "led_on",
    "led_off",
    "operation_time_up",
    "operation_time_down",
)


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


def _overlay_user_fields(
    store_modules: dict[str, dict[str, Any]],
    legacy_flat: dict[str, dict[str, Any]],
) -> tuple[int, int, list[str]]:
    """Apply user-editable fields from the legacy file onto matching Store entries.

    Returns ``(modules_overlaid, channels_overlaid, no_match)`` where
    ``no_match`` is the list of legacy addresses with no Store counterpart.
    Channels are matched by index — legacy channels beyond the Store's
    channel-count are silently dropped, which is the correct behaviour for
    cases like the 05-057 channel-3/4 over-count fix.
    """
    modules_overlaid = 0
    channels_overlaid = 0
    no_match: list[str] = []

    for legacy_addr, legacy_entry in legacy_flat.items():
        store_entry = store_modules.get(str(legacy_addr).upper())
        if not isinstance(store_entry, dict):
            no_match.append(legacy_addr)
            continue

        modules_overlaid += 1
        for field in _USER_MODULE_FIELDS:
            value = legacy_entry.get(field)
            if value not in (None, ""):
                store_entry[field] = value

        legacy_channels = legacy_entry.get("channels") or []
        store_channels = store_entry.get("channels") or []
        if not isinstance(legacy_channels, list) or not isinstance(store_channels, list):
            continue
        for index, legacy_channel in enumerate(legacy_channels):
            if index >= len(store_channels):
                # Legacy file claims more channels than discovery — drop the
                # extras rather than letting them re-introduce phantom
                # entities (e.g. the 05-057 4→2 channel-count correction).
                break
            store_channel = store_channels[index]
            if not isinstance(legacy_channel, dict) or not isinstance(store_channel, dict):
                continue
            for field in _USER_CHANNEL_FIELDS:
                value = legacy_channel.get(field)
                if value not in (None, ""):
                    store_channel[field] = value
                    channels_overlaid += 1

    return modules_overlaid, channels_overlaid, no_match


async def async_migrate_legacy_module_config(
    hass: HomeAssistant, store: NikobusModuleStorage
) -> bool:
    """Run the one-shot migration.

    Returns ``True`` when something was applied (full import or overlay),
    ``False`` otherwise (no legacy file present or file unreadable).

    Side effects on success:
      * **Empty Store path** — ``store.data["nikobus_module"]`` is replaced
        with the converted entries.
      * **Populated Store path** — user-editable fields from the legacy
        file are overlaid onto matching Store entries by address + channel
        index. Discovery-owned fields (``model``, ``module_type``,
        ``discovered_info``, channel count) are NOT touched.
      * In both cases ``store.async_save()`` is awaited and the source
        file is renamed to ``<config>/<LEGACY_FILENAME>.migrated``.
    """
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
    backup_path = source_path + _MIGRATED_SUFFIX

    if store.is_empty:
        if not flat:
            _LOGGER.info(
                "Legacy module config at %s contained no convertible entries — "
                "leaving store empty and renaming the source anyway",
                source_path,
            )
        store.data["nikobus_module"] = flat
        await store.async_save()
        _try_rename(source_path, backup_path, len(flat), "imported")
    else:
        store_modules = store.data.setdefault("nikobus_module", {})
        modules_overlaid, channels_overlaid, no_match = _overlay_user_fields(
            store_modules, flat
        )
        if modules_overlaid or channels_overlaid:
            await store.async_save()
        _LOGGER.info(
            "Overlaid user-edited fields from %s onto Store: modules=%d channels=%d "
            "no_match=%d (Store had %d entries before).",
            source_path,
            modules_overlaid,
            channels_overlaid,
            len(no_match),
            len(store_modules),
        )
        if no_match:
            _LOGGER.info(
                "Legacy addresses with no Store counterpart (skipped): %s",
                no_match,
            )
        _try_rename(source_path, backup_path, modules_overlaid, "overlaid")

    return True


def _try_rename(source_path: str, backup_path: str, count: int, action: str) -> None:
    """Rename the legacy file out of the way; log loudly on failure."""
    try:
        os.replace(source_path, backup_path)
    except OSError as err:
        _LOGGER.warning(
            "%s %d modules from %s, but could not rename to %s: %s. "
            "The integration will now ignore the source file; rename or "
            "remove it manually.",
            action.capitalize(),
            count,
            source_path,
            backup_path,
            err,
        )
        return
    _LOGGER.info(
        "%s %d modules from %s. Source renamed to %s.",
        action.capitalize(),
        count,
        source_path,
        backup_path,
    )
