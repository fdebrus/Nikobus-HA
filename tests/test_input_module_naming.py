"""Regression tests for synthesized input-module naming.

PC-Logic (05-201) and Modular Interface (05-206) inputs share the same
``pc_logic_parent_*`` provenance fields. The bug fixed here: both were
labelled ``LM-INPUT N`` because the naming code only checked
``pc_logic_parent_address`` and ignored ``pc_logic_parent_type``. The
"LM" (Logic Module) prefix is PC-Logic-specific; the Modular Interface
must use "MI".
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

COMP = Path(__file__).parent.parent / "custom_components" / "nikobus"


def _ensure_stubs() -> None:
    if "homeassistant.components.button" not in sys.modules:
        m = types.ModuleType("homeassistant.components.button")
        m.__spec__ = importlib.machinery.ModuleSpec(
            "homeassistant.components.button", None
        )
        m.ButtonEntity = type("ButtonEntity", (), {})
        sys.modules["homeassistant.components.button"] = m

    for mod_name in ("homeassistant.const", "homeassistant.helpers.entity"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "EntityCategory"):
            if not hasattr(mod.EntityCategory, "CONFIG"):
                mod.EntityCategory.CONFIG = "config"


def _load_button_module():
    _ensure_stubs()
    if "custom_components.nikobus.entity" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "custom_components.nikobus.entity", COMP / "entity.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "custom_components.nikobus"
        sys.modules["custom_components.nikobus.entity"] = mod
        spec.loader.exec_module(mod)

    if "custom_components.nikobus.button" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "custom_components.nikobus.button", COMP / "button.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "custom_components.nikobus"
        sys.modules["custom_components.nikobus.button"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["custom_components.nikobus.button"]


def _load_router_module():
    _ensure_stubs()
    if "custom_components.nikobus.router" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "custom_components.nikobus.router", COMP / "router.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "custom_components.nikobus"
        sys.modules["custom_components.nikobus.router"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["custom_components.nikobus.router"]


# --- input_label_prefix (router) ------------------------------------------

def test_prefix_pc_logic_is_lm() -> None:
    router = _load_router_module()
    assert router.input_label_prefix({"pc_logic_parent_type": "pc_logic"}) == "LM"


def test_prefix_interface_module_is_mi() -> None:
    router = _load_router_module()
    assert (
        router.input_label_prefix({"pc_logic_parent_type": "interface_module"})
        == "MI"
    )


def test_prefix_missing_type_defaults_to_lm() -> None:
    # Back-compat: legacy entries without the discriminator stay LM.
    router = _load_router_module()
    assert router.input_label_prefix({}) == "LM"


# --- pc_logic_input_naming (device name, router) --------------------------

def test_device_name_pc_logic() -> None:
    router = _load_router_module()
    name, via = router.pc_logic_input_naming(
        {
            "pc_logic_parent_address": "940c",
            "pc_logic_parent_type": "pc_logic",
            "pc_logic_slot_index": 3,
        }
    )
    assert name == "LM-INPUT 3"
    assert via == (router.DOMAIN, "940C")


def test_device_name_interface_module() -> None:
    router = _load_router_module()
    name, via = router.pc_logic_input_naming(
        {
            "pc_logic_parent_address": "1234",
            "pc_logic_parent_type": "interface_module",
            "pc_logic_slot_index": 5,
        }
    )
    assert name == "MI-INPUT 5"
    assert via == (router.DOMAIN, "1234")


def test_device_name_non_input_returns_none() -> None:
    router = _load_router_module()
    assert router.pc_logic_input_naming({"type": "Bus push button"}) is None


# --- op_point_display_name (per-key name) ---------------------------------

def test_key_name_pc_logic() -> None:
    button = _load_button_module()
    parent = {
        "pc_logic_parent_address": "940C",
        "pc_logic_parent_type": "pc_logic",
        "pc_logic_slot_index": 1,
    }
    assert (
        button.op_point_display_name("64A061", "1A", {}, parent_phys=parent)
        == "Key A on LM-INPUT 1"
    )
    assert (
        button.op_point_display_name("64A061", "1B", {}, parent_phys=parent)
        == "Key B on LM-INPUT 1"
    )


def test_key_name_interface_module() -> None:
    button = _load_button_module()
    parent = {
        "pc_logic_parent_address": "1234",
        "pc_logic_parent_type": "interface_module",
        "pc_logic_slot_index": 2,
    }
    assert (
        button.op_point_display_name("0E1234", "1A", {}, parent_phys=parent)
        == "Key A on MI-INPUT 2"
    )
    assert (
        button.op_point_display_name("0E1234", "1B", {}, parent_phys=parent)
        == "Key B on MI-INPUT 2"
    )
