"""Helpers for tests that inspect a config-flow form's schema."""

from __future__ import annotations

from typing import Any


def schema_fields(data_schema: Any) -> Any:
    """The ``{marker: validator}`` dict of a form schema, real or stubbed.

    The real ``vol.Schema`` keeps it on ``.schema``; the conftest stub's
    ``Schema`` returns the dict itself.
    """
    return getattr(data_schema, "schema", data_schema)
