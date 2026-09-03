"""Device-registry helpers shared by every platform (no integration imports)."""

from __future__ import annotations

from typing import Any


def parent_device_id(
    device_registry: Any, entry_id: str, identifier: tuple[str, str]
) -> str | None:
    """Registry id of the device carrying ``identifier``, or ``None``.

    Device parents are expressed as ``via_device_id`` (the registry id
    of the parent), so the parent must already be registered — which
    the setup order guarantees (hub → categories → modules / plates →
    their children). ``None`` when the registry is unavailable or the
    parent doesn't exist yet; the child is then created without a
    parent link rather than failing.
    """
    if device_registry is None:
        return None
    lookup = getattr(device_registry, "async_get_device_by_identifier", None)
    if lookup is None:
        return None
    device = lookup(identifier, entry_id)
    return device.id if device is not None else None
