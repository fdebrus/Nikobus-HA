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


# ---------------------------------------------------------------------------
# Friendly-name overlay (2.11.5+)
# ---------------------------------------------------------------------------
#
# Distinct from ``_apply_module_config`` / ``_apply_button_config`` above,
# which fully REPLACE the stores from the manual files (used when there's
# no PC-Link to provide inventory). The overlay enriches existing entries
# in-place: same address present in both the live store AND the file →
# copy user-editable fields (descriptions, entity_type, roller times,
# LED triggers) from file onto the store entry. Modules in the file that
# aren't in the store are silently ignored — PC-Link's inventory is the
# authority for what exists.
#
# Use case: PC-Link installs whose users want their custom labels (from
# an old YAML/JSON setup or from a sibling install) to survive the bus
# discovery's generic "Switch Module" / "Output 1" defaults.

_MODULE_OVERLAY_FIELDS = ("description",)
_CHANNEL_OVERLAY_FIELDS = (
    "description",
    "entity_type",
    "operation_time_up",
    "operation_time_down",
    "led_on",
    "led_off",
)


def _overlay_module(store_entry: dict[str, Any], file_entry: dict[str, Any]) -> bool:
    """Apply user-editable fields from ``file_entry`` onto ``store_entry``.

    Module-level fields (currently just ``description``) and the
    per-channel fields listed in ``_CHANNEL_OVERLAY_FIELDS``. Matches
    channels by INDEX (file's channel 0 → store's channel 0). Returns
    True if any field was overwritten.
    """
    changed = False

    for field in _MODULE_OVERLAY_FIELDS:
        if field in file_entry and file_entry[field] not in ("", None):
            if store_entry.get(field) != file_entry[field]:
                store_entry[field] = file_entry[field]
                changed = True

    file_channels = file_entry.get("channels") or []
    store_channels = store_entry.setdefault("channels", [])
    for idx, file_ch in enumerate(file_channels):
        if not isinstance(file_ch, dict):
            continue
        # Extend store channel list if file declares more than discovery
        # found — won't happen for real installs but keeps the overlay
        # robust against partial-discovery edge cases.
        while idx >= len(store_channels):
            store_channels.append({})
        store_ch = store_channels[idx]
        for field in _CHANNEL_OVERLAY_FIELDS:
            if field in file_ch and file_ch[field] not in ("", None):
                if store_ch.get(field) != file_ch[field]:
                    store_ch[field] = file_ch[field]
                    changed = True

    return changed


async def async_apply_friendly_name_overlay(
    hass: HomeAssistant,
    module_store: NikobusModuleStorage,
    button_data: dict[str, Any],
) -> bool:
    """Overlay user-editable fields from the manual config files onto the
    existing live stores. Does NOT add or remove modules/buttons — only
    enriches entries that already exist (typically populated by PC-Link
    inventory).

    Returns True if any field was changed in either store (signal to
    the caller to persist).
    """
    changed_any = False

    # Module overlay
    path, payload = await _read_json(hass, MANUAL_MODULE_CONFIG_FILENAME)
    if path is not None and isinstance(payload, dict):
        flat = convert_legacy_to_flat(payload)
        store_modules = module_store.data.setdefault("nikobus_module", {})
        applied = skipped = 0
        for addr, file_entry in flat.items():
            store_entry = store_modules.get(addr)
            if store_entry is None:
                skipped += 1
                continue
            if _overlay_module(store_entry, file_entry):
                applied += 1
                changed_any = True
        _LOGGER.info(
            "Friendly-name overlay: %d module(s) updated from %s "
            "(%d file entries had no matching live module — ignored).",
            applied, path, skipped,
        )

    # Button overlay — match by physical button address + op-point key.
    path, payload = await _read_json(hass, MANUAL_BUTTON_CONFIG_FILENAME)
    if path is not None and isinstance(payload, dict):
        raw = payload.get("nikobus_button")
        legacy_iter: list[dict[str, Any]] = []
        if isinstance(raw, list):
            legacy_iter = [e for e in raw if isinstance(e, dict)]
        elif isinstance(raw, dict):
            for addr, entry in raw.items():
                if isinstance(entry, dict):
                    merged = dict(entry)
                    merged.setdefault("address", addr)
                    legacy_iter.append(merged)

        store_buttons = button_data.setdefault("nikobus_button", {})
        applied = skipped = 0
        for entry in legacy_iter:
            description = str(entry.get("description") or "").strip()
            physical = _extract_physical_info(entry)
            if physical is None:
                # Synthetic / IR / scene entry — match its standalone
                # single-key store entry by bus_address.
                bus_addr = str(entry.get("address") or "").strip().upper()
                store_btn = store_buttons.get(bus_addr)
                if store_btn is None or not description:
                    skipped += 1
                    continue
                op = store_btn.get("operation_points", {}).get("1A")
                if op is not None and op.get("description") != description:
                    op["description"] = description
                    store_btn["description"] = description
                    applied += 1
                    changed_any = True
                continue

            phys_addr, key_label, _channels, _type, _model = physical
            store_btn = store_buttons.get(phys_addr)
            if store_btn is None:
                skipped += 1
                continue
            op = store_btn.get("operation_points", {}).get(key_label)
            if op is None:
                skipped += 1
                continue
            if description and op.get("description") != description:
                op["description"] = description
                applied += 1
                changed_any = True

        _LOGGER.info(
            "Friendly-name overlay: %d op-point(s) updated from %s "
            "(%d file entries had no matching live op-point — ignored).",
            applied, path, skipped,
        )

    return changed_any


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

    button_data["nikobus_button"] = _consolidate_legacy_1a_only_buttons(new_buttons)
    return len(new_buttons), op_points_total, path


# ---------------------------------------------------------------------------
# Legacy 1A-only → canonical multi-key consolidation (2.11.8+)
# ---------------------------------------------------------------------------
#
# Manual button-config files authored before the multi-key format list each
# physical wall-button key face as a SEPARATE ``channels=1`` entry with a
# single ``1A`` operation point — e.g. a 4-key wall plate becomes 4
# unrelated 1-channel entries. A PC-Link inventory would produce ONE
# ``channels=4`` entry with ``1A``/``1B``/``1C``/``1D`` op-points instead.
#
# The wall-button bus-address encoding is fully reversible: for a 4-key
# button the top 2 bits of the first hex nibble encode the key face (per
# ``nikobus_connect.discovery.mapping.KEY_MAPPING``), and the remaining
# 22 bits identify the physical button. For 8-key buttons the top 3 bits
# carry the key, leaving 21 bits of identity. We use that to find sibling
# 1A-only entries and merge them into the canonical multi-key form.
#
# Scope: this runs ONLY from the no-PC-Link manual-import path
# (``_apply_button_config`` → ``async_apply_manual_config`` →
# coordinator's ``_apply_manual_inventory_as_fallback``). PC-Link installs
# never hit it; their inventory arrives in canonical form from the library.

# Key-face offset → label, indexed by channel count. Mirror of the
# library's KEY_MAPPING with the offset as the key for fast lookup.
_KEY_OFFSET_TO_LABEL: dict[int, dict[int, str]] = {
    1: {0x8: "1A"},
    2: {0x8: "1A", 0xC: "1B"},
    4: {0x8: "1A", 0xC: "1B", 0x0: "1C", 0x4: "1D"},
    8: {
        0xA: "1A", 0xE: "1B", 0x2: "1C", 0x6: "1D",
        0x8: "2A", 0xC: "2B", 0x0: "2C", 0x4: "2D",
    },
}

# Physical-id mask per channel count. The bits NOT in the mask encode
# the key face: 4-key uses top 2 bits of first nibble (mask 0x3FFFFF =
# 22-bit identity), 8-key uses top 3 bits (mask 0x1FFFFF = 21-bit
# identity). 1- and 2-key share the 22-bit mask since their offsets
# all live in the top-2-bits-of-first-nibble space.
_PHYSICAL_ID_MASK: dict[int, int] = {1: 0x3FFFFF, 2: 0x3FFFFF, 4: 0x3FFFFF, 8: 0x1FFFFF}


def _addr_int(addr: str) -> int | None:
    try:
        return int(addr, 16)
    except ValueError:
        return None


def _physical_id_for(addr: str, channels: int) -> str | None:
    val = _addr_int(addr)
    mask = _PHYSICAL_ID_MASK.get(channels)
    if val is None or mask is None:
        return None
    return f"{val & mask:06X}"


def _key_offset_for(addr: str, channels: int) -> int | None:
    """Extract the key-face offset value from the first nibble.

    4-key: top 2 bits → offset ∈ {0x0, 0x4, 0x8, 0xC}.
    8-key: top 3 bits → offset ∈ {0x0, 0x2, 0x4, 0x6, 0x8, 0xA, 0xC, 0xE}.
    """
    if not addr:
        return None
    try:
        nib = int(addr[0], 16)
    except ValueError:
        return None
    if channels == 8:
        return nib & 0xE
    return nib & 0xC


def _try_group(addresses: list[str], channels: int) -> dict[str, str] | None:
    """Return ``{address: key_label}`` if ``addresses`` form a clean
    group for the given channel count. ``None`` on any mismatch — the
    caller falls back to leaving the entries singleton.

    8-key extra check: KEY_MAPPING[8]'s offsets all have bit 0 = 0
    (values 0,2,4,6,8,A,C,E are all even). A real 8-key button's
    addresses therefore all have first-nibble bit 0 = 0. Without this
    guard, two 4-key wall buttons sharing the same lower 21 bits but
    differing in first-nibble bit 0 would falsely look like one 8-key
    button — that's exactly the Living_buro case in the user's data.
    """
    labels = _KEY_OFFSET_TO_LABEL.get(channels)
    if labels is None or len(addresses) != channels:
        return None
    expected = set(labels.keys())
    actual: dict[str, int] = {}
    for a in addresses:
        try:
            first_nibble = int(a[0], 16)
        except (ValueError, IndexError):
            return None
        if channels == 8 and (first_nibble & 0x1) != 0:
            return None
        offset = _key_offset_for(a, channels)
        if offset is None or offset not in expected:
            return None
        actual[a] = offset
    if set(actual.values()) != expected:
        return None
    return {a: labels[off] for a, off in actual.items()}


def _is_consolidation_candidate(entry: dict[str, Any]) -> bool:
    """True iff ``entry`` is a 1A-only single-face fallback that could
    be merged into a larger multi-key group."""
    if entry.get("channels") != 1:
        return False
    ops = entry.get("operation_points")
    if not isinstance(ops, dict) or list(ops.keys()) != ["1A"]:
        return False
    return True


def _common_description_prefix(descriptions: list[str]) -> str:
    """Longest shared starting substring, trimmed of trailing separators."""
    cleaned = [d for d in descriptions if d]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    common = cleaned[0]
    for s in cleaned[1:]:
        while common and not s.startswith(common):
            common = common[:-1]
        if not common:
            return ""
    return common.rstrip("_- .")


def _build_consolidated_entry(
    source_buttons: dict[str, dict[str, Any]],
    label_map: dict[str, str],
    channels: int,
) -> dict[str, Any]:
    """Build a canonical multi-key button entry from N 1A-only siblings.

    Each sibling's ``description`` and ``bus_address`` move into the
    matching op-point. ``linked_modules`` is left empty — step 2 will
    populate links from the bus. Carries the first sibling's
    ``type`` / ``model`` if set; uses a common description prefix
    when one exists, else the first sibling's description.
    """
    op_points: dict[str, Any] = {}
    descriptions: list[str] = []
    type_str = ""
    model = ""
    for addr, label in label_map.items():
        src = source_buttons[addr]
        op = src.get("operation_points", {}).get("1A", {})
        desc = str(src.get("description") or op.get("description") or "").strip()
        descriptions.append(desc)
        op_points[label] = {
            "bus_address": addr,
            "description": desc or f"#N{addr}",
            "linked_modules": [],
        }
        type_str = type_str or str(src.get("type") or "").strip()
        model = model or str(src.get("model") or "").strip()

    parent_desc = _common_description_prefix(descriptions) or (descriptions[0] if descriptions else "")
    return {
        "type": type_str or "Push button",
        "model": model,
        "channels": channels,
        "description": parent_desc,
        "operation_points": op_points,
        "discovered_info": {"source": "manual_config_consolidated"},
    }


def _consolidate_legacy_1a_only_buttons(
    buttons: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group legacy 1A-only per-face entries into canonical multi-key
    buttons by reversing the address-bit encoding.

    Best-effort: candidates that don't form a clean 1/2/4/8-key group
    stay as singletons. Already-multi-key entries (``channels > 1``)
    are untouched — the existing grouping wins. Entries whose inferred
    ``physical_id`` collides with an existing non-candidate entry are
    also left alone to avoid clobbering authored data.

    Only invoked from the no-PC-Link manual-import path.
    """
    # Index candidates by 21-bit physical_id (for 8-key matching) and
    # by 22-bit physical_id (for 1/2/4-key). A given address goes into
    # both indexes; the 8-key pass below claims first when applicable.
    by_21: dict[str, list[str]] = {}
    by_22: dict[str, list[str]] = {}
    for addr, entry in buttons.items():
        if not _is_consolidation_candidate(entry):
            continue
        pid_22 = _physical_id_for(addr, 4)
        if pid_22 is not None:
            by_22.setdefault(pid_22, []).append(addr)
        pid_21 = _physical_id_for(addr, 8)
        if pid_21 is not None:
            by_21.setdefault(pid_21, []).append(addr)

    consumed: set[str] = set()
    new_entries: dict[str, dict[str, Any]] = {}

    # Pass 1 — 8-key groups (most constrained; claim these first so
    # they don't get mis-claimed as a 4-key + half-group of strays).
    for pid, members in by_21.items():
        if len(members) != 8:
            continue
        if pid in buttons and not _is_consolidation_candidate(buttons[pid]):
            continue
        label_map = _try_group(members, 8)
        if label_map is None:
            continue
        new_entries[pid] = _build_consolidated_entry(buttons, label_map, 8)
        consumed.update(members)

    # Pass 2 — 4/2-key groups from the 22-bit index, skipping anything
    # already consumed by the 8-key pass. Singletons (would-be 1-key
    # groups) stay as-is; consolidating them changes nothing and would
    # mask runtime-auto-add provenance.
    for pid, members in by_22.items():
        rem = [a for a in members if a not in consumed]
        if not rem:
            continue
        if pid in buttons and not _is_consolidation_candidate(buttons[pid]):
            continue
        for n in (4, 2):
            if len(rem) != n:
                continue
            label_map = _try_group(rem, n)
            if label_map is None:
                continue
            new_entries[pid] = _build_consolidated_entry(buttons, label_map, n)
            consumed.update(rem)
            break

    if not consumed:
        return buttons

    out: dict[str, dict[str, Any]] = {}
    for addr, entry in buttons.items():
        if addr in consumed:
            continue
        if addr in new_entries:
            # Singleton at the physical_id itself — defensive; the
            # consolidated form is the canonical one to keep.
            continue
        out[addr] = entry
    out.update(new_entries)
    _LOGGER.info(
        "Manual-config: consolidated %d 1A-only button faces into "
        "%d canonical multi-key button(s).",
        len(consumed), len(new_entries),
    )
    return out


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
