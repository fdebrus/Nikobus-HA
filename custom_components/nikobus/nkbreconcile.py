"""Post-discovery reconciliation: the library's store helpers, plus the
one rule that is Home Assistant's — which central functions become
scene entities.

Member sets, the ``controlled_by`` index, button status, central
function classification, the routing graph and the project file's
colour-controller links are questions about the shape of the library's
own records, and since nikobus-connect 0.49.0 they are answered in
``nikobus_connect.discovery.store``. The names are re-exported here so
the platforms and tests keep their imports.
"""

from __future__ import annotations

from typing import Any

from nikobus_connect.discovery.store import (
    INPUT_ONLY_BUTTON_TYPES,
    REGISTRY_SOURCES,
    all_outputs_registry_sourced,
    apply_rgb_links,
    build_controlled_by_index,
    build_routing_graph,
    cf_cover_members,
    cf_member_set,
    classify_button_status,
    collect_button_linked_modules,
    collect_button_outputs,
    flatten_cf_broadcasts,
    has_pc_logic_module,
    is_button_backed_cf,
    is_pure_roller_cf,
    member_set_from_outputs,
)
from nikobus_connect.nkb import mode_code


def is_surfaced_cf_scene(cf: dict[str, Any]) -> bool:
    """True if a CF should be surfaced as an HA *scene* entity.

    Single source of truth shared by the scene platform (what it creates)
    and the coordinator's known-id set (what orphan cleanup keeps), so the
    two can't drift.

    A button-backed light-scene is only surfaced once it has been **named**
    by a matching named group in the ``.nkb`` project file. An unnamed one
    is just a button the user already has on the bus — surfacing it as a
    scene would duplicate that button (issue: unnamed phantom scenes such
    as ``829201`` after a plain module scan). The bare ``38xx`` central
    functions always surface (named or not).

    Pure-roller CFs are handled separately — they become grouped covers,
    not scenes — so callers must apply :func:`is_pure_roller_cf` first.
    """
    if is_button_backed_cf(cf):
        name = (cf or {}).get("name")
        return isinstance(name, str) and bool(name.strip())
    return True


__all__ = [
    "INPUT_ONLY_BUTTON_TYPES",
    "REGISTRY_SOURCES",
    "all_outputs_registry_sourced",
    "apply_rgb_links",
    "build_controlled_by_index",
    "build_routing_graph",
    "cf_cover_members",
    "cf_member_set",
    "classify_button_status",
    "collect_button_linked_modules",
    "collect_button_outputs",
    "flatten_cf_broadcasts",
    "has_pc_logic_module",
    "is_button_backed_cf",
    "is_pure_roller_cf",
    "is_surfaced_cf_scene",
    "member_set_from_outputs",
    "mode_code",
]
