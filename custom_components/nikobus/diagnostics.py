"""Diagnostics support for the Nikobus integration."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .coordinator import NikobusConfigEntry
from .entity import device_entry_diagnostics

TO_REDACT = {CONF_CONNECTION_STRING}


def _per_module_decode_metrics(coordinator) -> dict[str, dict[str, Any]]:
    """Build per-output-module decode-quality metrics.

    For each module address surface:
      * ``channel_count`` — channels the catalogue ascribes to this module
      * ``link_record_count`` — total decoded link records routed to it
      * ``triggering_buttons`` — number of distinct physical buttons that
        drive at least one of its channels
      * ``unique_modes`` — set of M-codes seen across its records
      * ``channels_with_links`` / ``channels_without_links`` — how many
        of the catalogued channels have at least one decoded link record
        vs none (channels with zero links are either unprogrammed or
        residue from a previous install)
      * ``unique_t1_values`` / ``unique_t2_values`` — distinct resolved
        timer strings (post-decode, after the per-mode T1/T2 tables)

    No bus-specific assumptions — pure aggregation of what the discovery
    decoder has already produced. Surfaces the same info on any install.
    """

    out: dict[str, dict[str, Any]] = {}

    # Build (module, channel) → list[button-record] index by reusing
    # the coordinator's own helper. Returns [] for unknown channels.
    buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
    if not isinstance(buttons, dict):
        buttons = {}

    # Collect every (module_address, channel, mode, t1, t2, button_phys)
    records: list[dict[str, Any]] = []
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, dict):
            continue
        for key_label, op in op_points.items():
            if not isinstance(op, dict):
                continue
            for link in op.get("linked_modules") or []:
                if not isinstance(link, dict):
                    continue
                module_address = link.get("module_address")
                if not isinstance(module_address, str):
                    continue
                module_key = module_address.upper()
                for output in link.get("outputs") or []:
                    if not isinstance(output, dict):
                        continue
                    channel = output.get("channel")
                    if not isinstance(channel, int):
                        continue
                    records.append({
                        "module": module_key,
                        "channel": channel,
                        "mode": output.get("mode"),
                        "t1": output.get("t1"),
                        "t2": output.get("t2"),
                        "button_phys": physical_addr,
                    })

    # Group by module
    by_module: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_module.setdefault(r["module"], []).append(r)

    # Resolve channel counts from dict_module_data
    channel_counts: dict[str, int] = {}
    for _bucket, modules in (coordinator.dict_module_data or {}).items():
        if not isinstance(modules, dict):
            continue
        for addr, meta in modules.items():
            if not isinstance(meta, dict):
                continue
            cnt = meta.get("channel_count") or meta.get("channels")
            try:
                channel_counts[addr.upper()] = int(cnt) if cnt is not None else 0
            except (TypeError, ValueError):
                channel_counts[addr.upper()] = 0

    for module_addr, recs in by_module.items():
        ch_total = channel_counts.get(module_addr, 0)
        channels_seen: set[int] = {r["channel"] for r in recs}
        buttons_seen: set[str] = {r["button_phys"] for r in recs}
        modes_seen: Counter = Counter(
            r["mode"] for r in recs if r["mode"] is not None
        )
        t1_values = sorted({r["t1"] for r in recs if r["t1"]})
        t2_values = sorted({r["t2"] for r in recs if r["t2"]})
        out[module_addr] = {
            "channel_count": ch_total,
            "link_record_count": len(recs),
            "triggering_buttons": len(buttons_seen),
            "channels_with_links": len(channels_seen),
            "channels_without_links": max(0, ch_total - len(channels_seen)),
            "unique_modes": sorted(modes_seen.keys()),
            "mode_distribution": dict(modes_seen),
            "unique_t1_values": t1_values,
            "unique_t2_values": t2_values,
        }

    # Also surface modules that are in the inventory but have zero
    # decoded link records — useful for spotting modules that were
    # discovered but produced no programming.
    for module_addr, ch_total in channel_counts.items():
        if module_addr in out:
            continue
        out[module_addr] = {
            "channel_count": ch_total,
            "link_record_count": 0,
            "triggering_buttons": 0,
            "channels_with_links": 0,
            "channels_without_links": ch_total,
            "unique_modes": [],
            "mode_distribution": {},
            "unique_t1_values": [],
            "unique_t2_values": [],
        }

    return out


def _button_decode_metrics(coordinator) -> dict[str, Any]:
    """Top-level button-store decode metrics.

    Surfaces aggregate counts useful for sanity-checking what came out
    of discovery: total operation points, how many have at least one
    linked module vs none, how many are PC-Logic-synthesized inputs
    vs real wall buttons, etc.
    """

    buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
    if not isinstance(buttons, dict):
        return {
            "physical_button_count": 0,
            "operation_point_count": 0,
            "op_points_with_links": 0,
            "op_points_without_links": 0,
            "synthesized_input_count": 0,
        }

    op_total = 0
    op_with_links = 0
    op_without_links = 0
    synthesized = 0
    for phys in buttons.values():
        if not isinstance(phys, dict):
            continue
        if phys.get("pc_logic_parent_address"):
            synthesized += 1
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, dict):
            continue
        for op in op_points.values():
            if not isinstance(op, dict):
                continue
            op_total += 1
            if op.get("linked_modules"):
                op_with_links += 1
            else:
                op_without_links += 1
    return {
        "physical_button_count": len(buttons),
        "operation_point_count": op_total,
        "op_points_with_links": op_with_links,
        "op_points_without_links": op_without_links,
        "synthesized_input_count": synthesized,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NikobusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Nikobus config entry."""
    coordinator = entry.runtime_data
    device_registry = dr.async_get(hass)

    nikobus_devices = [
        device
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if any(ident[0] == DOMAIN for ident in device.identifiers)
    ]

    raw_module_states = {
        addr: state.hex() for addr, state in coordinator.nikobus_module_states.items()
    }

    return {
        "config_entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "state": entry.state.value,
            "feedback_module": entry.options.get(
                CONF_HAS_FEEDBACK_MODULE,
                entry.data.get(CONF_HAS_FEEDBACK_MODULE, False),
            ),
            "prior_gen3": entry.options.get(
                CONF_PRIOR_GEN3, entry.data.get(CONF_PRIOR_GEN3, False)
            ),
            "refresh_interval": entry.options.get(
                CONF_REFRESH_INTERVAL,
                entry.data.get(CONF_REFRESH_INTERVAL, 0),
            ),
        },
        "coordinator": {
            "connection_status": coordinator.connection_status,
            "reconnect_attempts": coordinator._reconnect_attempts,
            "last_connected": (
                coordinator._last_connected.isoformat()
                if coordinator._last_connected
                else None
            ),
            "module_count": len(coordinator.dict_module_data),
            "button_count": len(
                coordinator.dict_button_data.get("nikobus_button", {})
            ),
            "scene_count": len(coordinator.dict_scene_data.get("scene", [])),
            "discovery_phase": coordinator.discovery_phase,
            "raw_hex_states": raw_module_states,
        },
        "discovery_quality": {
            "per_module": _per_module_decode_metrics(coordinator),
            "buttons": _button_decode_metrics(coordinator),
        },
        "devices": [device_entry_diagnostics(dev) for dev in nikobus_devices],
    }
