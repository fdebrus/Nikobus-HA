#!/usr/bin/env python3
"""Preview how the .nkb scenes import will bucket YOUR named groups.

Runs entirely on your machine against your own files — nothing is sent
anywhere. It classifies every named Central Function group in your .nkb
into the three buckets the new import logic uses:

  A  matches an already-discovered CF broadcast  -> KEPT, named from .nkb
  B  fired only by a real button on the bus      -> SKIPPED (left as the button)
  C  no on-bus trigger at all                     -> SKIPPED (nothing to activate)

Bucket B is the behavior change: those groups used to be created as a
separate scene that duplicated a button; now they're skipped. This tells
you, before merging, how many of your named scenes that affects.

Requirements:
    pip install nikobus-connect      # the same parser the integration uses

Usage:
    python nkb_scene_buckets.py /path/to/homeassistant/config

    # config dir is where your .nkb lives and which has a .storage/ folder
    # with nikobus.cfs and nikobus.buttons. You can also pass paths
    # explicitly:
    python nkb_scene_buckets.py \
        --nkb /path/to/project.nkb \
        --cfs /path/to/.storage/nikobus.cfs \
        --buttons /path/to/.storage/nikobus.buttons
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nikobus_connect.nkb import find_nkb_file, mode_code, parse_nkb


# --- the two pure helpers the integration uses, copied verbatim so this
# --- script needs only nikobus-connect, not the HA custom component ----------

def member_set_from_outputs(outputs):
    out = set()
    for o in outputs or []:
        if not isinstance(o, dict):
            continue
        mod = o.get("module_address")
        ch = o.get("channel")
        code = mode_code(o.get("mode"))
        if isinstance(mod, str) and isinstance(ch, int) and code:
            out.add((mod.upper(), ch, code))
    return frozenset(out)


def cf_member_set(cf):
    return member_set_from_outputs((cf or {}).get("outputs"))


def build_routing_graph(button_data):
    graph = {}
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
            outputs, seen = [], set()
            for link in op.get("linked_modules") or []:
                if not isinstance(link, dict):
                    continue
                mod = link.get("module_address")
                if not isinstance(mod, str):
                    continue
                for o in link.get("outputs") or []:
                    if not isinstance(o, dict):
                        continue
                    ch, m = o.get("channel"), o.get("mode")
                    if not (isinstance(ch, int) and isinstance(m, str)):
                        continue
                    key = (mod.upper(), ch, m)
                    if key in seen:
                        continue
                    seen.add(key)
                    outputs.append({"module_address": mod.upper(), "channel": ch, "mode": m})
            members = member_set_from_outputs(outputs)
            if not members:
                continue
            graph.setdefault(members, ([], outputs))[0].append(addr.upper())
    return {m: (sorted(set(a)), o) for m, (a, o) in graph.items()}


def _load_store(path: Path):
    """Read an HA .storage file ({"data": {...}}) or a bare dict."""
    if not path or not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return raw.get("data", raw) if isinstance(raw, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config_dir", nargs="?", help="HA config dir (holds the .nkb and .storage/)")
    ap.add_argument("--nkb", help="explicit path to the .nkb")
    ap.add_argument("--cfs", help="explicit path to .storage/nikobus.cfs")
    ap.add_argument("--buttons", help="explicit path to .storage/nikobus.buttons")
    args = ap.parse_args()

    cfg = Path(args.config_dir) if args.config_dir else None
    nkb_path = Path(args.nkb) if args.nkb else (find_nkb_file(str(cfg)) if cfg else None)
    if not nkb_path or not Path(nkb_path).exists():
        print("ERROR: could not find a .nkb — pass --nkb or a config dir.", file=sys.stderr)
        return 2
    cfs_path = Path(args.cfs) if args.cfs else (cfg / ".storage" / "nikobus.cfs" if cfg else None)
    btn_path = Path(args.buttons) if args.buttons else (cfg / ".storage" / "nikobus.buttons" if cfg else None)

    data = parse_nkb(Path(nkb_path))
    cf_store = _load_store(cfs_path) if cfs_path else {}
    btn_store = _load_store(btn_path) if btn_path else {}

    cf_member_sets = {
        cf_member_set(cf)
        for cf in (cf_store.get("nikobus_cf", {}) or {}).values()
        if isinstance(cf, dict)
    }
    graph = build_routing_graph(btn_store)

    A, B, C = [], [], []
    for sc in data.scenes:
        if not sc.members:
            continue
        if sc.members in cf_member_sets:
            A.append(sc.name)
        elif sc.members in graph:
            B.append((sc.name, graph[sc.members][0]))
        else:
            C.append(sc.name)

    print(f"\n.nkb           : {nkb_path}")
    print(f"CF store       : {cfs_path}  ({'found' if cfs_path and cfs_path.exists() else 'MISSING'})")
    print(f"Button store   : {btn_path}  ({'found' if btn_path and btn_path.exists() else 'MISSING'})")
    print(f"Named groups   : {len(data.scenes)}\n")

    print(f"A — KEPT & named (matches a discovered CF) : {len(A)}")
    for n in sorted(A):
        print(f"      • {n}")
    print(f"\nB — SKIPPED (button-fired; was a duplicate): {len(B)}")
    for n, addrs in sorted(B):
        print(f"      • {n}   [trigger(s): {', '.join(addrs)}]")
    print(f"\nC — SKIPPED (no on-bus trigger)            : {len(C)}")
    for n in sorted(C):
        print(f"      • {n}")

    if not cf_store:
        print("\nNOTE: CF store missing/empty — every group will look like B/C. "
              "Run a discovery first so nikobus.cfs is populated, or pass --cfs.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
