"""Read user-given names + scenes from a Nikobus ``.nkb`` project file.

A ``.nkb`` is a ZIP holding ``__niko__.mdb`` — an MS Access (JET)
database. We surface three things for the integration to apply:

* **addresses** — ``{ADDRESS: (name, room)}`` for every module / button /
  IR receiver (``Component`` keyed by ``PhysicalAddress``, room from
  ``Location``). Applied as suggested device/entity names + HA Areas.
* **scenes** — each Central Function group (``Scene - Dinner`` …) with the
  set of output members that realise it, so we can match a named group to
  a discovered CF entity by **member set** (the group has no bus address
  of its own, but its trigger's output links spell out exactly which
  ``(module, channel, mode)`` it drives — identical to what discovery
  reads from the modules).

Everything is best-effort: a malformed/unsupported ``.nkb`` raises and the
caller degrades gracefully. Only ``construct`` is needed at runtime (the
Access reader is vendored).
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

_LOGGER = logging.getLogger(__name__)

#: One parsed ``.mdb`` row: column name → value.
_Row = dict[str, Any]

CANONICAL_NKB_FILENAME = "nikobus.nkb"

# Hard ceiling on the decompressed ``.mdb`` we'll read from a ``.nkb``.
# A real Nikobus project DB is a few MB; this only exists to bound a
# crafted (decompression-bomb) archive. Generous on purpose.
_MAX_MDB_BYTES = 64 * 1024 * 1024  # 64 MiB

# Location bucket the software uses for virtual groups (scenes), not a room.
_GROUP_LOCATION_SENTINEL = "S_DB_GROUPS"

# Connection mode that links an input to a Central Function group.
_MCF_MODE = "MCF"

_MODE_CODE_RE = re.compile(r"M\d+", re.IGNORECASE)

# Output object prefix → channel number (``O01`` → 1, ``O12`` → 12).
_OUTPUT_PREFIX_RE = re.compile(r"^O(\d+)$", re.IGNORECASE)


def _fmt_addr(physical_address: int) -> str:
    """Format a ``PhysicalAddress`` to match our bus-address identifiers.

    Module addresses are 16-bit → 4 hex (``0E6C``); button / IR / RF
    addresses are 24-bit → 6 hex (``1843B4``). Matching the natural width
    is essential: our device identifiers use ``0E6C``, not ``000E6C``.
    """
    v = physical_address & 0xFFFFFF
    return f"{v:04X}" if v < 0x10000 else f"{v:06X}"


def _mode_code(mode: object) -> str | None:
    """Leading ``M<n>`` code of a mode string (``"M12 (Preset on)"`` ->
    ``"M12"``; ``"M12"`` -> ``"M12"``), or ``None``."""
    if not isinstance(mode, str):
        return None
    m = _MODE_CODE_RE.match(mode.strip())
    return m.group(0).upper() if m else None


class SceneDef(NamedTuple):
    """A named Central Function group and the outputs it drives."""

    name: str
    #: ``frozenset`` of ``(module_addr_upper, channel, mode_code)``.
    members: frozenset[tuple[str, int, str]]


class NkbData(NamedTuple):
    """Everything we extract from a ``.nkb``."""

    #: ``{ADDRESS_HEX_UPPER: (name, room)}`` — room is ``""`` if none.
    addresses: dict[str, tuple[str, str]]
    #: Named scene groups with member sets, for member-set matching.
    scenes: list[SceneDef]
    #: ``{(MODULE_ADDR_UPPER, channel): name}`` — per-output channel names
    #: (the light / cover / switch the user actually toggles). Read-only, so
    #: the empty-dict default is safe.
    outputs: dict[tuple[str, int], str] = {}


# Generic per-output placeholders in the .nkb that aren't real names.
_OUTPUT_PLACEHOLDERS = frozenset(
    {"output", "switch output", "shutter output", "dimmer output"}
)


def find_nkb_file(config_dir: str) -> Path | None:
    """Return the ``.nkb`` to import from ``config_dir``, or ``None``.

    Prefers the canonical ``nikobus.nkb``; otherwise a single ``*.nkb``.
    Declines (``None``) when several ``*.nkb`` exist and none is canonical.
    """
    base = Path(config_dir)
    canonical = base / CANONICAL_NKB_FILENAME
    if canonical.is_file():
        return canonical
    candidates = sorted(base.glob("*.nkb"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        _LOGGER.warning(
            "Multiple .nkb files in %s (%s) — rename the one to import to %s",
            config_dir,
            [p.name for p in candidates],
            CANONICAL_NKB_FILENAME,
        )
    return None


def parse_nkb(nkb_path: str | Path) -> NkbData:
    """Parse ``nkb_path``. Blocking — run in an executor.

    Raises on a genuinely unreadable file (bad zip / no mdb / parser
    failure); the caller is expected to catch and degrade gracefully.
    """
    from .vendor.access_parser import AccessParser

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(nkb_path) as zf:
            mdb_name = next(
                (n for n in zf.namelist() if n.lower().endswith(".mdb")), None
            )
            if mdb_name is None:
                raise ValueError("no .mdb inside the .nkb archive")
            # Read the .mdb through a hard byte cap and write it under a
            # FIXED local name. Two reasons, both defending against a
            # crafted .nkb:
            #   * the byte cap stops a decompression-bomb member from
            #     exhausting memory/disk (``read`` decompresses lazily, so
            #     a 10 GB member only ever yields ``_MAX_MDB_BYTES`` here);
            #   * the fixed output name means an attacker-controlled member
            #     path (``/etc/x.mdb``, ``../../x.mdb``) can't redirect the
            #     AccessParser read outside ``tmp`` — unlike ``Path(tmp) /
            #     mdb_name``, which an absolute or ``..`` name escapes.
            with zf.open(mdb_name) as member:
                data = member.read(_MAX_MDB_BYTES + 1)
            if len(data) > _MAX_MDB_BYTES:
                raise ValueError(
                    f".mdb exceeds the {_MAX_MDB_BYTES}-byte safety limit"
                )
            mdb_path = Path(tmp) / "project.mdb"
            mdb_path.write_bytes(data)
        db = AccessParser(str(mdb_path))
        components = _rows(db, "Component")
        locations = {
            r["KeyLocation"]: r["StrUserName"] for r in _rows(db, "Location")
        }
        objecten = _rows(db, "Objecten")
        connections = _rows(db, "Connection")
        linkmodes = {
            r["KeyLinkMode"]: r.get("StrMode") for r in _rows(db, "LinkModeBase")
        }
        objectbase = {r["KeyObjectBase"]: r for r in _rows(db, "ObjectBase")}

    comp_by_key = {c["KeyComponent"]: c for c in components}

    addresses = _extract_addresses(components, locations)
    scenes = _extract_scenes(
        components, comp_by_key, objecten, connections, linkmodes, objectbase
    )
    outputs = _extract_outputs(comp_by_key, objecten, objectbase)
    return NkbData(addresses=addresses, scenes=scenes, outputs=outputs)


def _extract_outputs(
    comp_by_key: dict[Any, _Row], objecten: list[_Row], objectbase: dict[Any, _Row]
) -> dict[tuple[str, int], str]:
    """``{(MODULE_ADDR, channel): name}`` for output channels with a real
    user name. Channel is the output's ``Prefix`` number (``O02`` → 2);
    generic placeholders (``Output``, ``Switch output``…) are skipped."""
    out: dict[tuple[str, int], str] = {}
    for o in objecten:
        comp = comp_by_key.get(o.get("KeyComponent"), {})
        pa = comp.get("PhysicalAddress")
        if not (isinstance(pa, int) and 0 < pa < 0x10000):
            continue  # output modules are 16-bit (4-hex) addresses
        base = objectbase.get(o.get("KeyObjectBase"), {})
        m = _OUTPUT_PREFIX_RE.match(str(base.get("Prefix") or ""))
        if not m:
            continue
        name = (o.get("StrUserName") or "").strip()
        if not name or name.lower() in _OUTPUT_PLACEHOLDERS:
            continue
        out[(_fmt_addr(pa), int(m.group(1)))] = name
    return out


def _extract_addresses(
    components: list[_Row], locations: dict[Any, Any]
) -> dict[str, tuple[str, str]]:
    """``{ADDRESS: (name, room)}`` for the physically-addressed components."""
    out: dict[str, tuple[str, str]] = {}
    for comp in components:
        name = (comp.get("StrUserName") or "").strip()
        if not name:
            continue
        pa = comp.get("PhysicalAddress")
        if not (isinstance(pa, int) and pa > 0):
            continue  # -1 == a scene group (no bus address)
        room = locations.get(comp.get("KeyLocation")) or ""
        if room == _GROUP_LOCATION_SENTINEL:
            room = ""
        out[_fmt_addr(pa)] = (name, room)
    return out


def _extract_scenes(
    components: list[_Row],
    comp_by_key: dict[Any, _Row],
    objecten: list[_Row],
    connections: list[_Row],
    linkmodes: dict[Any, Any],
    objectbase: dict[Any, _Row],
) -> list[SceneDef]:
    """Resolve each named CF group to its ``(module, channel, mode)`` members.

    Group → MCF connection → trigger input object → that input's output
    connections (the real link records) → members. The group object itself
    carries only the trigger link; the membership lives on the trigger.
    """
    obj_by_key = {o["KeyObject"]: o for o in objecten}
    objs_by_component: dict[Any, set[Any]] = {}
    for o in objecten:
        objs_by_component.setdefault(o.get("KeyComponent"), set()).add(o["KeyObject"])
    conns_by_in: dict[Any, list[_Row]] = {}
    for cn in connections:
        conns_by_in.setdefault(cn["KeyObjectIn"], []).append(cn)

    def module_addr(obj: _Row | None) -> str | None:
        comp = comp_by_key.get((obj or {}).get("KeyComponent"), {})
        pa = comp.get("PhysicalAddress")
        return _fmt_addr(pa) if isinstance(pa, int) and pa > 0 else None

    def channel(obj: _Row | None) -> int | None:
        # Channel = the output's ``Prefix`` number (``O01`` -> 1), which
        # matches Home Assistant's per-channel numbering for EVERY module
        # type. (``ObjectAddress`` can't be used: roller outputs occupy
        # pairs, so a roller module's ``ObjectAddress`` runs 0,2,4,… while
        # HA numbers the rollers 1,2,3,… — the prefix is the aligned index.)
        base = objectbase.get((obj or {}).get("KeyObjectBase"), {})
        m = _OUTPUT_PREFIX_RE.match(str(base.get("Prefix") or ""))
        return int(m.group(1)) if m else None

    scenes: list[SceneDef] = []
    for comp in components:
        if comp.get("PhysicalAddress") != -1:
            continue
        name = (comp.get("StrUserName") or "").strip()
        if not name:
            continue
        group_objs = objs_by_component.get(comp["KeyComponent"], set())

        # Trigger input objects = the IN side of each MCF connection whose
        # OUT side is one of the group's objects.
        triggers = {
            cn["KeyObjectIn"]
            for cn in connections
            if cn["KeyObjectOut"] in group_objs
            and linkmodes.get(cn["KeyLinkMode"]) == _MCF_MODE
        }

        members: set[tuple[str, int, str]] = set()
        for trig in triggers:
            for cn in conns_by_in.get(trig, []):
                code = _mode_code(linkmodes.get(cn["KeyLinkMode"]))
                if code is None:  # MCF / unknown — not an output member
                    continue
                out_obj = obj_by_key.get(cn["KeyObjectOut"])
                ma = module_addr(out_obj)
                ch = channel(out_obj)
                if ma and ch is not None:
                    members.add((ma, ch, code))

        if members:
            scenes.append(SceneDef(name=name, members=frozenset(members)))
    return scenes


def _rows(db: Any, table: str) -> list[_Row]:
    """Row-dicts for ``table`` (access_parser returns column->list)."""
    parsed = db.parse_table(table)
    cols = list(parsed.keys())
    n = len(next(iter(parsed.values()))) if cols else 0
    return [{c: parsed[c][i] for c in cols} for i in range(n)]
