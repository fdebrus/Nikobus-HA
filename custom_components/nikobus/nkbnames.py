"""Read user-given names from a Nikobus ``.nkb`` project file.

A ``.nkb`` is a ZIP holding ``__niko__.mdb`` — an MS Access (JET)
database. The Nikobus PC software stores every module / button / IR
receiver under a user-given name in the ``Component`` table, keyed by
``PhysicalAddress`` (the decimal of the 24-bit bus address), with the
room in ``Location``. Central Functions / scenes appear as ``Component``
rows with ``PhysicalAddress == -1`` (no bus address — named only here).

We surface the addressable names as ``{ADDRESS: "Name (Room)"}`` for the
integration to apply as suggested device / entity names, plus the
address-less scene names for reference.

Everything here is best-effort: parsing is wrapped so a malformed or
unsupported ``.nkb`` never breaks the integration — the caller logs and
skips. The Access reader is vendored (``.vendor.access_parser``) and the
only runtime dependency is ``construct``.
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from pathlib import Path
from typing import NamedTuple

_LOGGER = logging.getLogger(__name__)

# Canonical filename we look for first; otherwise any single *.nkb.
CANONICAL_NKB_FILENAME = "nikobus.nkb"

# A room bucket the software uses for virtual groups (scenes); not a real room.
_GROUP_LOCATION_SENTINEL = "S_DB_GROUPS"


class NkbNames(NamedTuple):
    """Result of parsing a ``.nkb``."""

    #: ``{ADDRESS_HEX_UPPER: "Name (Room)"}`` for modules / buttons / receivers.
    addresses: dict[str, str]
    #: ``{scene_name: room}`` for Central Functions with no bus address.
    scenes: dict[str, str]


def find_nkb_file(config_dir: str) -> Path | None:
    """Return the ``.nkb`` to import from ``config_dir``, or ``None``.

    Prefers the canonical ``nikobus.nkb``; otherwise accepts a single
    ``*.nkb`` in the directory. With several ``*.nkb`` and no canonical
    one we decline (ambiguous) and return ``None``.
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


def parse_nkb_names(nkb_path: str | Path) -> NkbNames:
    """Parse ``nkb_path`` and return its names. Blocking — run in executor.

    Raises on a genuinely unreadable file (bad zip / no mdb / parser
    failure); the caller is expected to catch and degrade gracefully.
    """
    # Lazy import so a parser/construct issue can't break integration load.
    from .vendor.access_parser import AccessParser

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(nkb_path) as zf:
            mdb_name = next(
                (n for n in zf.namelist() if n.lower().endswith(".mdb")), None
            )
            if mdb_name is None:
                raise ValueError("no .mdb inside the .nkb archive")
            zf.extract(mdb_name, tmp)
        db = AccessParser(str(Path(tmp) / mdb_name))
        components = _rows(db, "Component")
        locations = {
            r["KeyLocation"]: r["StrUserName"] for r in _rows(db, "Location")
        }

    addresses: dict[str, str] = {}
    scenes: dict[str, str] = {}
    for comp in components:
        name = (comp.get("StrUserName") or "").strip()
        if not name:
            continue
        room = locations.get(comp.get("KeyLocation")) or ""
        room_label = "" if room == _GROUP_LOCATION_SENTINEL else room
        pa = comp.get("PhysicalAddress")
        if isinstance(pa, int) and pa > 0:
            addr = f"{pa & 0xFFFFFF:06X}"
            addresses[addr] = f"{name} ({room_label})" if room_label else name
        else:
            # PhysicalAddress == -1 → a Central Function / scene group.
            scenes[name] = room_label

    return NkbNames(addresses=addresses, scenes=scenes)


def _rows(db, table: str) -> list[dict]:
    """Row-dicts for ``table`` (access_parser returns column->list)."""
    parsed = db.parse_table(table)
    cols = list(parsed.keys())
    n = len(next(iter(parsed.values()))) if cols else 0
    return [{c: parsed[c][i] for c in cols} for i in range(n)]
