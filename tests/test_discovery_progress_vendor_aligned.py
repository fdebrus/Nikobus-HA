"""Tests for the 0.16.1-aware discovery progress handler.

The library's vendor-aligned scan plan reads non-contiguous registers
across multiple passes per module, so the pre-0.16.1 HA-side math
(``done = register - 0x10 + 1``) produces wildly wrong percentages.
0.16.1+ surfaces ``registers_sent`` directly — these tests pin that
the HA handler uses it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock


# Conftest sets up the coordinator stubs; we just need to import the
# coordinator module after conftest has run.


@dataclass
class _FakeProgress:
    """Lightweight stand-in for ``nikobus_connect.discovery.DiscoveryProgress``."""

    phase: str = "register_scan"
    module_address: str | None = None
    module_index: int = 0
    module_total: int = 0
    register: int | None = None
    register_total: int = 0
    registers_sent: int = 0
    pass_index: int = 0
    pass_total: int = 0
    sub_byte: str | None = None
    decoded_records: int = 0


def _make_coordinator():
    """Build a minimal coordinator with the progress handler attached."""
    from custom_components.nikobus.coordinator import NikobusDataCoordinator

    coord = MagicMock(spec=NikobusDataCoordinator)
    coord.discovery_phase = "scan"
    coord.discovery_status_message = ""
    coord.discovery_sub_phase = "idle"
    coord.discovery_decoded_records = 0
    coord.discovery_register_current = None
    coord._SUB_TO_LEGACY_PHASE = {"register_scan": "scan"}
    update = MagicMock()
    coord._update_discovery_state = update

    # Bind the real method to the mock so it actually runs.
    coord._handle_discovery_progress = (
        NikobusDataCoordinator._handle_discovery_progress.__get__(coord)
    )
    return coord, update


def test_handler_uses_registers_sent_directly() -> None:
    """When ``registers_sent`` is provided, ratio is registers_sent/total."""
    coord, update = _make_coordinator()
    progress = _FakeProgress(
        phase="register_scan",
        module_address="C7C1",
        module_index=1,
        module_total=3,
        register=0x70,
        register_total=48,
        registers_sent=10,
        pass_index=2,
        pass_total=3,
        sub_byte="01",
    )
    asyncio.run(coord._handle_discovery_progress(progress))
    update.assert_called_once()
    kwargs = update.call_args.kwargs
    assert kwargs["registers_done"] == 10
    assert kwargs["registers_total"] == 48


def test_handler_falls_back_to_register_minus_10_when_no_registers_sent() -> None:
    """Pre-0.16.1 progress events (no registers_sent) fall back to the
    legacy contiguous-from-0x10 calculation."""
    coord, update = _make_coordinator()
    progress = _FakeProgress(
        phase="register_scan",
        module_address="C7C1",
        module_index=1,
        module_total=3,
        register=0x15,
        register_total=64,
        registers_sent=0,  # not supplied → fallback
    )
    asyncio.run(coord._handle_discovery_progress(progress))
    kwargs = update.call_args.kwargs
    # 0x15 - 0x10 + 1 = 6
    assert kwargs["registers_done"] == 6
    assert kwargs["registers_total"] == 64


def test_handler_status_message_includes_pass_info_for_vendor_plan() -> None:
    """Multi-pass scans surface ``pass N/M sub=XX`` in the user-facing
    status message so the UI doesn't look frozen between passes."""
    coord, update = _make_coordinator()
    progress = _FakeProgress(
        phase="register_scan",
        module_address="C7C1",
        module_index=2,
        module_total=5,
        register=0x70,
        register_total=48,
        registers_sent=15,
        pass_index=2,
        pass_total=3,
        sub_byte="01",
    )
    asyncio.run(coord._handle_discovery_progress(progress))
    message = update.call_args.kwargs["message"]
    assert "C7C1" in message
    assert "(2/5)" in message
    assert "pass 2/3" in message
    assert "sub=01" in message
    assert "15/48" in message


def test_handler_status_message_omits_pass_info_for_single_pass() -> None:
    """PC-Link's 1-pass scan doesn't clutter the message with pass info."""
    coord, update = _make_coordinator()
    progress = _FakeProgress(
        phase="register_scan",
        module_address="8C49",
        module_index=1,
        module_total=1,
        register=0xA3,
        register_total=93,
        registers_sent=1,
        pass_index=1,
        pass_total=1,
        sub_byte="04",
    )
    asyncio.run(coord._handle_discovery_progress(progress))
    message = update.call_args.kwargs["message"]
    assert "pass" not in message.lower()
    assert "8C49" in message


def test_handler_no_register_total_uses_240_fallback() -> None:
    """Forensic-mode scans may not supply register_total — fallback
    keeps the bar from going to infinity."""
    coord, update = _make_coordinator()
    progress = _FakeProgress(
        phase="register_scan",
        module_address="C7C1",
        module_index=1,
        module_total=1,
        register=0x20,
        register_total=0,
        registers_sent=0,
    )
    asyncio.run(coord._handle_discovery_progress(progress))
    kwargs = update.call_args.kwargs
    assert kwargs["registers_total"] == 240


def test_handler_resets_counters_on_sub_phase_transition() -> None:
    """2.11.0: when the library transitions from identity to register_scan
    (or any sub-phase change), the HA-side handler must zero out the
    cached register counters so the first emit of the new phase doesn't
    display the previous phase's leftover total. Without this, the
    identity end-state of 96/96 leaks into the first register-scan
    event of the first module, showing 100%."""
    coord, update = _make_coordinator()
    # Simulate identity phase end-state: 96/96 cached.
    coord.discovery_sub_phase = "identity"
    coord.discovery_registers_done = 96
    coord.discovery_registers_total = 96

    # First register-scan emit arrives. nikobus-connect 0.19.1 resets
    # register_total to 0 at the identity-end, so the emit has 0/0
    # initially. The HA handler must NOT carry forward the cached 96/96.
    progress = _FakeProgress(
        phase="register_scan",
        module_address="C9A5",
        module_index=1,
        module_total=8,
        register=None,
        register_total=0,
        registers_sent=0,
    )
    asyncio.run(coord._handle_discovery_progress(progress))
    kwargs = update.call_args.kwargs
    # Before this fix, registers_done would have been carried over from
    # identity (96) — now it's 0.
    assert kwargs["registers_done"] == 0
    # Fallback to 240 when register_total=0 (legacy/forensic guard)
    # is still applied, but the cached identity-phase total is gone.
    assert kwargs["registers_total"] == 240


def _percent_coord(scope, sub_phase, *, regs_done=0, regs_total=0,
                   mods_done=0, mods_total=0):
    """Bare coordinator exercising the discovery_progress_percent property."""
    from custom_components.nikobus.coordinator import NikobusDataCoordinator

    c = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    c._discovery_scope = scope
    c.discovery_sub_phase = sub_phase
    c.discovery_phase = "scan"
    c.discovery_registers_done = regs_done
    c.discovery_registers_total = regs_total
    c.discovery_modules_done = mods_done
    c.discovery_modules_total = mods_total
    return c


def test_module_scan_progress_starts_at_zero() -> None:
    """Load Existing Installation (register_scan only) opens at 0 %, not 30 %."""
    c = _percent_coord("module_scan", "register_scan",
                       regs_done=0, regs_total=48, mods_done=0, mods_total=4)
    assert c.discovery_progress_percent == 0.0


def test_module_scan_progress_reaches_full_range() -> None:
    """Last module fully scanned → ~95 % within the register phase (the
    remaining 5 % is finalizing), spanning the whole bar not 30→95."""
    c = _percent_coord("module_scan", "register_scan",
                       regs_done=48, regs_total=48, mods_done=3, mods_total=4)
    # 4/4 of the register phase maps to (95-30)/70*100 ≈ 92.9
    assert c.discovery_progress_percent > 90.0


def test_module_scan_finalizing_near_complete() -> None:
    c = _percent_coord("module_scan", "finalizing")
    # finalizing midpoint: raw 97.5 → (97.5-30)/70*100 ≈ 96.4
    assert 95.0 <= c.discovery_progress_percent <= 99.9


def test_inventory_scope_spans_full_bar() -> None:
    """Load Project Overview (inventory+identity) spans 0→100 on its own."""
    c = _percent_coord("inventory", "identity",
                       regs_done=0, regs_total=0, mods_done=1, mods_total=1)
    # identity done=1/1 → raw 30 → /30*100 = 100 → capped 99.9
    assert c.discovery_progress_percent == 99.9


def test_full_scope_unchanged() -> None:
    """A combined run keeps the stacked value (register_scan floor 30)."""
    c = _percent_coord("full", "register_scan",
                       regs_done=0, regs_total=48, mods_done=0, mods_total=4)
    assert c.discovery_progress_percent == 30.0
