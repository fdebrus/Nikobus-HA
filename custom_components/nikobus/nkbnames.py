"""Compatibility shim — the ``.nkb`` parser now lives in the library.

As of ``nikobus-connect`` 0.26.0 the Nikobus ``.nkb`` project-file reader
(and its vendored MS-Access parser) live in ``nikobus_connect.nkb``,
alongside the bus-frame and PC-Link record parsers. This module just
re-exports that public API so the integration's call sites and tests
keep importing from ``.nkbnames``.

The *apply* side — writing names/Areas into the HA registry and matching
scenes to discovered CF entities — stays in the integration
(``coordinator.py`` / ``config_flow.py``).
"""

from __future__ import annotations

from nikobus_connect.nkb import (
    CANONICAL_NKB_FILENAME,
    NkbData,
    SceneDef,
    find_nkb_file,
    mode_code as _mode_code,
    parse_nkb,
)

__all__ = [
    "CANONICAL_NKB_FILENAME",
    "NkbData",
    "SceneDef",
    "find_nkb_file",
    "parse_nkb",
    "_mode_code",
]
