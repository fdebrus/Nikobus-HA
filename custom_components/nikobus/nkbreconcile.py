"""Pure post-discovery reconciliation helpers.

Stateless data-crunching extracted from ``coordinator.py``: member-set
keys (used to match ``.nkb`` scene groups, classified CF entries and
routing-graph op-points against each other), the ``controlled_by``
index, and the registry-only-residue checks. None of these touch Home
Assistant — they take the integration's button/module store dicts and
return plain data — so they live here, away from the coordinator's HA
lifecycle, and are unit-tested in isolation.
"""

from __future__ import annotations

from typing import Any

from .const import INPUT_ONLY_BUTTON_TYPES

# ``_mode_code`` extracts the leading ``M<n>`` from a mode label. It's
# re-exported by the integration's ``nkbnames`` module (which since
# nikobus-connect 0.26.0 is a thin shim over ``nikobus_connect.nkb``),
# so this stays correct whether the parser is local or library-provided.
from .nkbnames import _mode_code as mode_code

# A button whose every output record is registry-sourced (read from a
# PC-Link / PC-Logic register table rather than an output module's own
# link table) is residue from a previous owner's programming — *unless*
# the install has a PC-Logic, where it may be a legitimate scene trigger.
REGISTRY_SOURCES = frozenset({"pc_link_registry", "pc_logic_registry"})


def member_set_from_outputs(outputs: Any) -> frozenset[tuple[str, int, str]]:
    """Frozenset of ``(module_upper, channel, mode_code)`` for an output
    list — the canonical key for matching a scene/CF by its members, used
    identically on ``.nkb`` groups, CF entries and routing-graph op-points
    so the three are directly comparable."""
    out: set[tuple[str, int, str]] = set()
    for o in outputs or []:
        if not isinstance(o, dict):
            continue
        mod = o.get("module_address")
        ch = o.get("channel")
        code = mode_code(o.get("mode"))
        if isinstance(mod, str) and isinstance(ch, int) and code:
            out.add((mod.upper(), ch, code))
    return frozenset(out)


def cf_member_set(cf: dict[str, Any]) -> frozenset[tuple[str, int, str]]:
    """Member-set key for a stored ``nikobus_cf`` entry."""
    return member_set_from_outputs((cf or {}).get("outputs"))


def collect_button_linked_modules(phys: dict[str, Any]) -> set[str]:
    """Union of every module address referenced by any of a button's op-points."""
    linked: set[str] = set()
    op_points = phys.get("operation_points") or {}
    if not isinstance(op_points, dict):
        return linked
    for op_point in op_points.values():
        if not isinstance(op_point, dict):
            continue
        for link in op_point.get("linked_modules") or []:
            if not isinstance(link, dict):
                continue
            addr = link.get("module_address")
            if addr:
                linked.add(str(addr).upper())
    return linked


def collect_button_outputs(phys: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every output record under every op-point of a button.

    Returned dicts are the decoder's per-output records (channel, mode,
    payload, button_address, plus nikobus-connect 0.5.22+'s
    ``record_source``). Used by the registry-only residue check.
    """
    outputs: list[dict[str, Any]] = []
    op_points = phys.get("operation_points") or {}
    if not isinstance(op_points, dict):
        return outputs
    for op_point in op_points.values():
        if not isinstance(op_point, dict):
            continue
        for link in op_point.get("linked_modules") or []:
            if not isinstance(link, dict):
                continue
            for out in link.get("outputs") or []:
                if isinstance(out, dict):
                    outputs.append(out)
    return outputs


def all_outputs_registry_sourced(outputs: list[dict[str, Any]]) -> bool:
    """True iff every output has ``record_source`` in the registry set.

    Returns False if ``outputs`` is empty, or if any output is missing
    the field. Pre-0.5.22 records (no ``record_source``) are treated as
    source-unknown and fall through to the existing classifier —
    backward compat without data migration.
    """
    if not outputs:
        return False
    return all(out.get("record_source") in REGISTRY_SOURCES for out in outputs)


def has_pc_logic_module(module_data: dict[str, Any] | None) -> bool:
    """True if the install has at least one PC-Logic module in the store.

    Gates the registry-only residue verdict: with PC-Logic absent, a
    button whose every output is registry-sourced is unambiguous residue
    (no real button-to-output link exists anywhere). With PC-Logic
    present, the same shape could be a legitimate PC-Logic-only scene
    trigger — fall through to the existing classifier and let the user
    adjudicate.
    """
    modules = (module_data or {}).get("nikobus_module", {})
    if not isinstance(modules, dict):
        return False
    return any(
        isinstance(m, dict) and m.get("module_type") == "pc_logic"
        for m in modules.values()
    )


def build_controlled_by_index(
    button_data: dict[str, Any] | None,
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Build a ``(module_address_upper, channel) -> [button record]`` index."""
    index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    buttons = (button_data or {}).get("nikobus_button", {})
    if not isinstance(buttons, dict):
        return index
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, dict):
            continue
        for key_label, op_point in op_points.items():
            if not isinstance(op_point, dict):
                continue
            bus_addr = op_point.get("bus_address") or ""
            description = op_point.get("description") or f"Button {bus_addr}"
            for link in op_point.get("linked_modules") or []:
                if not isinstance(link, dict):
                    continue
                module_address = link.get("module_address")
                if not module_address:
                    continue
                module_key = str(module_address).upper()
                for out in link.get("outputs") or []:
                    if not isinstance(out, dict):
                        continue
                    channel = out.get("channel")
                    if not isinstance(channel, int):
                        continue
                    index.setdefault((module_key, channel), []).append({
                        "bus_address": bus_addr,
                        "description": description,
                        "mode": out.get("mode"),
                        "t1": out.get("t1"),
                        "t2": out.get("t2"),
                        "wall_button_address": physical_addr,
                        "wall_button_key": key_label,
                    })
    return index


def classify_button_status(
    phys: dict[str, Any],
    remaining_modules: set[str],
    has_pc_logic: bool,
) -> str:
    """Return the post-discovery reconciliation bucket for one button.

    ``remaining_modules`` is the set of surviving (non-evicted) module
    addresses (upper-case); ``has_pc_logic`` is the install's topology
    gate. One of:

      * ``synthesized_input`` — a library-synthesized PC-Logic (05-201) /
        Modular-Interface (05-206) input child (``pc_logic_parent_address``
        set). Models a bus-event source the parent listens to internally;
        empty ``linked_modules`` is its steady state, not residue.
      * ``input_only`` — a Universal Interface (05-058) and friends
        (``type`` in ``INPUT_ONLY_BUTTON_TYPES``): emits press telegrams
        but never writes output-module link tables. Empty links is normal.
      * ``legacy_undecoded`` — no decoded outputs anywhere (pre-Stage-2
        default, or an intentionally-unwired HA-trigger button).
      * ``legacy_orphan`` — has decoded outputs but either every output is
        registry-sourced with no PC-Logic to justify it (residue from a
        previous owner), or every decoded-target module was evicted.
      * ``active`` — at least one linked module survived the scan.
    """
    if phys.get("pc_logic_parent_address"):
        return "synthesized_input"
    if phys.get("type") in INPUT_ONLY_BUTTON_TYPES:
        return "input_only"
    linked = collect_button_linked_modules(phys)
    outputs = collect_button_outputs(phys)
    if not outputs:
        return "legacy_undecoded"
    if not has_pc_logic and all_outputs_registry_sourced(outputs):
        return "legacy_orphan"
    if not (linked & remaining_modules):
        return "legacy_orphan"
    return "active"


def flatten_cf_broadcasts(broadcasts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert the library's ``CFBroadcast`` objects to the JSON-safe
    ``nikobus_cf`` store shape.

    ``{addr: {bus_address, pattern, outputs, triggered_by}}`` — ``outputs``
    is a list of ``{module_address, channel, mode, t1, t2}`` dicts, keyed
    addresses upper-cased.
    """
    flat: dict[str, dict[str, Any]] = {}
    for addr, cf in broadcasts.items():
        outputs = [
            {
                "module_address": str(m.module_address).upper(),
                "channel": int(m.channel),
                "mode": str(m.mode),
                "t1": getattr(m, "t1", None),
                "t2": getattr(m, "t2", None),
            }
            for m in getattr(cf, "outputs", [])
        ]
        bus_address = str(getattr(cf, "bus_address", addr)).upper()
        triggered_by = [
            str(t).upper()
            for t in (getattr(cf, "triggered_by", None) or [bus_address])
        ]
        flat[str(addr).upper()] = {
            "bus_address": bus_address,
            "pattern": str(getattr(cf, "pattern", "unknown")),
            "outputs": outputs,
            "triggered_by": triggered_by,
        }
    return flat


def cf_cover_members(cf: dict[str, Any]) -> list[dict[str, Any]]:
    """Collapse a *bidirectional* ``roller_pair`` CF into distinct cover members.

    A true 2-button roller central function bundles the open (``M02``) and
    close (``M03``) link records for the same channels, so each channel
    appears twice in ``outputs``. Returns one entry per distinct
    ``(module_address, channel)`` — preserving first-sighting order — with
    the open / close timing strings (``t1``) pulled from the matching mode::

        [{"module_address", "channel", "open_time", "close_time"}, ...]

    Returns ``[]`` (i.e. "not a cover") unless the CF carries **both** an
    ``M02`` and an ``M03`` record. Single-direction CFs (only-close /
    only-open) and 1-button ``M01`` ("open-stop-close" toggle) functions
    have no open+close pair to drive as a cover — a single broadcast is
    unambiguous for them — so they stay scenes. This keeps the cover path
    to genuine 2-button controls and avoids stranding M01/single-direction
    roller CFs (which would otherwise be filtered from the scene platform
    yet produce no cover).
    """
    members: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    seen_open = False
    seen_close = False
    for o in (cf or {}).get("outputs") or []:
        if not isinstance(o, dict):
            continue
        mod = o.get("module_address")
        ch = o.get("channel")
        if not (isinstance(mod, str) and isinstance(ch, int)):
            continue
        code = mode_code(o.get("mode"))
        if code not in ("M02", "M03"):
            continue
        key = (mod.upper(), ch)
        if key not in members:
            members[key] = {
                "module_address": mod.upper(),
                "channel": ch,
                "open_time": None,
                "close_time": None,
            }
            order.append(key)
        t1 = o.get("t1")
        if code == "M02":
            seen_open = True
            if members[key]["open_time"] is None:
                members[key]["open_time"] = t1
        else:  # M03
            seen_close = True
            if members[key]["close_time"] is None:
                members[key]["close_time"] = t1
    if not (seen_open and seen_close):
        return []
    return [members[k] for k in order]


def build_routing_graph(
    button_data: dict[str, Any] | None,
) -> dict[frozenset[tuple[str, int, str]], tuple[list[str], list[dict[str, Any]]]]:
    """Map every op-point's member set -> ``(firing addresses, outputs)``.

    The routing graph is the full set of ``trigger -> linked outputs``
    relations from the button store — the same data discovery decodes.
    Used to find the on-bus address that fires a named ``.nkb`` scene
    group (matched by member set), including the shutter / master groups
    that have no light-scene mode and so never become CF entities on
    their own. Addresses driving an identical output set (one scene,
    several triggers) are grouped; the sorted-first is the canonical
    activation address.
    """
    graph: dict[
        frozenset[tuple[str, int, str]], tuple[list[str], list[dict[str, Any]]]
    ] = {}
    buttons = (button_data or {}).get("nikobus_button", {})
    if not isinstance(buttons, dict):
        return {}
    for phys in buttons.values():
        if not isinstance(phys, dict):
            continue
        for op in (phys.get("operation_points") or {}).values():
            if not isinstance(op, dict):
                continue
            addr = op.get("bus_address")
            if not isinstance(addr, str) or not addr:
                continue
            outputs: list[dict[str, Any]] = []
            seen: set[tuple[str, int, str]] = set()
            for link in op.get("linked_modules") or []:
                if not isinstance(link, dict):
                    continue
                mod = link.get("module_address")
                if not isinstance(mod, str):
                    continue
                for o in link.get("outputs") or []:
                    if not isinstance(o, dict):
                        continue
                    ch = o.get("channel")
                    mode = o.get("mode")
                    if not (isinstance(ch, int) and isinstance(mode, str)):
                        continue
                    dedupe = (mod.upper(), ch, mode)
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    outputs.append(
                        {
                            "module_address": mod.upper(),
                            "channel": ch,
                            "mode": mode,
                            "t1": o.get("t1") if isinstance(o.get("t1"), str) else None,
                            "t2": o.get("t2") if isinstance(o.get("t2"), str) else None,
                        }
                    )
            members = member_set_from_outputs(outputs)
            if not members:
                continue
            entry = graph.setdefault(members, ([], outputs))
            entry[0].append(addr.upper())
    return {m: (sorted(set(a)), o) for m, (a, o) in graph.items()}
