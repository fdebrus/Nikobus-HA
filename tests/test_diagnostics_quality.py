"""Tests for the discovery-quality metrics in diagnostics.

These metrics aggregate post-discovery state into install-agnostic
numbers (link-record counts, triggering buttons, channels with/without
programming, mode distributions). They run on every install — no
specific catalogue or NKB-overlay assumption.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

COMP = Path(__file__).parent.parent / "custom_components" / "nikobus"


def _ensure_stubs() -> None:
    if "homeassistant.components.diagnostics" not in sys.modules:
        m = types.ModuleType("homeassistant.components.diagnostics")
        m.__spec__ = importlib.machinery.ModuleSpec(
            "homeassistant.components.diagnostics", None
        )
        m.async_redact_data = lambda data, redact_keys: {
            k: ("REDACTED" if k in redact_keys else v) for k, v in data.items()
        }
        sys.modules["homeassistant.components.diagnostics"] = m


def _load_diagnostics():
    _ensure_stubs()
    if "custom_components.nikobus.diagnostics" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "custom_components.nikobus.diagnostics", COMP / "diagnostics.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "custom_components.nikobus"
        sys.modules["custom_components.nikobus.diagnostics"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["custom_components.nikobus.diagnostics"]


class _FakeCoordinator:
    """Minimal stub exposing the attributes the metric helpers read."""

    def __init__(self, dict_module_data, dict_button_data):
        self.dict_module_data = dict_module_data
        self.dict_button_data = dict_button_data


def test_per_module_metrics_empty_install() -> None:
    """Zero modules → empty per-module dict."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(dict_module_data={}, dict_button_data={})
    result = diag._per_module_decode_metrics(coord)
    assert result == {}


def test_per_module_metrics_one_module_one_link() -> None:
    """Single button → single output channel → metrics reflect it."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={
            "switch_module": {
                "C7C1": {"channel_count": 12},
            },
        },
        dict_button_data={
            "nikobus_button": {
                "020080": {
                    "operation_points": {
                        "1A": {
                            "linked_modules": [
                                {
                                    "module_address": "C7C1",
                                    "outputs": [
                                        {
                                            "channel": 5,
                                            "mode": "M01 (On / off)",
                                            "t1": None,
                                            "t2": None,
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                },
            },
        },
    )
    result = diag._per_module_decode_metrics(coord)
    assert "C7C1" in result
    m = result["C7C1"]
    assert m["channel_count"] == 12
    assert m["link_record_count"] == 1
    assert m["triggering_buttons"] == 1
    assert m["channels_with_links"] == 1
    assert m["channels_without_links"] == 11
    assert m["unique_modes"] == ["M01 (On / off)"]


def test_per_module_metrics_aggregates_distinct_buttons_modes_timers() -> None:
    """Multiple buttons hitting same module → counts aggregate correctly."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={
            "switch_module": {
                "C7C1": {"channel_count": 12},
            },
        },
        dict_button_data={
            "nikobus_button": {
                "020080": {
                    "operation_points": {
                        "1A": {
                            "linked_modules": [{
                                "module_address": "C7C1",
                                "outputs": [
                                    {"channel": 1, "mode": "M01", "t1": None, "t2": None},
                                    {"channel": 2, "mode": "M06", "t1": "30 m", "t2": None},
                                ],
                            }],
                        },
                    },
                },
                "020081": {
                    "operation_points": {
                        "1B": {
                            "linked_modules": [{
                                "module_address": "C7C1",
                                "outputs": [
                                    {"channel": 1, "mode": "M03", "t1": "1 s", "t2": None},
                                ],
                            }],
                        },
                    },
                },
            },
        },
    )
    m = diag._per_module_decode_metrics(coord)["C7C1"]
    assert m["link_record_count"] == 3
    assert m["triggering_buttons"] == 2
    assert m["channels_with_links"] == 2  # channels 1 and 2
    assert m["channels_without_links"] == 10
    assert set(m["unique_modes"]) == {"M01", "M03", "M06"}
    assert set(m["unique_t1_values"]) == {"30 m", "1 s"}


def test_per_module_metrics_module_with_zero_records_still_surfaces() -> None:
    """Modules present in inventory but with no links surface as zero-record."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={
            "switch_module": {
                "C7C1": {"channel_count": 12},
            },
        },
        dict_button_data={"nikobus_button": {}},
    )
    result = diag._per_module_decode_metrics(coord)
    assert "C7C1" in result
    assert result["C7C1"]["link_record_count"] == 0
    assert result["C7C1"]["channels_without_links"] == 12


def test_per_module_metrics_handles_missing_channel_count() -> None:
    """Modules without a channel_count get 0 (not a crash)."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={
            "switch_module": {
                "C7C1": {},  # no channel_count / channels key
            },
        },
        dict_button_data={"nikobus_button": {}},
    )
    result = diag._per_module_decode_metrics(coord)
    assert result["C7C1"]["channel_count"] == 0


def test_button_metrics_counts_synthesized_inputs() -> None:
    """PC-Logic / 05-206 synthesized inputs are counted separately."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={},
        dict_button_data={
            "nikobus_button": {
                # Real wall button with linked outputs
                "020080": {
                    "operation_points": {
                        "1A": {"linked_modules": [{"outputs": [{"channel": 1}]}]},
                        "1B": {"linked_modules": []},
                    },
                },
                # PC-Logic synthesized input
                "640061": {
                    "pc_logic_parent_address": "8DC8",
                    "pc_logic_slot_index": 1,
                    "operation_points": {
                        "1A": {"linked_modules": []},
                        "1B": {"linked_modules": []},
                    },
                },
            },
        },
    )
    m = diag._button_decode_metrics(coord)
    assert m["physical_button_count"] == 2
    assert m["operation_point_count"] == 4
    assert m["op_points_with_links"] == 1
    assert m["op_points_without_links"] == 3
    assert m["synthesized_input_count"] == 1


def test_button_metrics_empty_install() -> None:
    """Empty install → all zeros."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={}, dict_button_data={"nikobus_button": {}}
    )
    m = diag._button_decode_metrics(coord)
    assert m == {
        "physical_button_count": 0,
        "operation_point_count": 0,
        "op_points_with_links": 0,
        "op_points_without_links": 0,
        "synthesized_input_count": 0,
        "input_only_count": 0,
    }


def test_per_module_metrics_module_address_normalised_uppercase() -> None:
    """Lookup by upper-case module address regardless of source casing."""
    diag = _load_diagnostics()
    coord = _FakeCoordinator(
        dict_module_data={
            "switch_module": {"c7c1": {"channel_count": 12}},
        },
        dict_button_data={
            "nikobus_button": {
                "020080": {
                    "operation_points": {
                        "1A": {
                            "linked_modules": [{
                                "module_address": "C7C1",
                                "outputs": [{"channel": 1, "mode": "M01"}],
                            }],
                        },
                    },
                },
            },
        },
    )
    result = diag._per_module_decode_metrics(coord)
    # Same module, regardless of source casing — one entry, not two
    assert "C7C1" in result
    assert result["C7C1"]["link_record_count"] == 1
