"""Regression tests for the device-registry category router.

The bug fixed here: ``"rf" in "interface".lower()`` is True (substring
match inside "inte**rf**ace"), so the previous RF-first ordering shoved
every Interface device into the Remotes category in the HA device list.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

COMP = Path(__file__).parent.parent / "custom_components" / "nikobus"


def _ensure_stubs() -> None:
    """Stubs for the few HA modules button.py touches at import time.

    conftest.py loads coordinator/sensor but not button.py; we lazily add
    the small extras the button module needs.
    """
    if "homeassistant.components.button" not in sys.modules:
        m = types.ModuleType("homeassistant.components.button")
        m.__spec__ = importlib.machinery.ModuleSpec(
            "homeassistant.components.button", None
        )
        m.ButtonEntity = type("ButtonEntity", (), {})
        sys.modules["homeassistant.components.button"] = m

    # conftest's EntityCategory stub only exposes DIAGNOSTIC; button.py
    # also references CONFIG on its inventory-trigger button.
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


def test_universal_interface_routes_to_interfaces() -> None:
    """``"rf"`` is a substring of ``"interface"`` — must not steal it."""
    button = _load_button_module()
    assert (
        button._category_for_button_type("Universal interface, 8 channels")
        == button.CATEGORY_INTERFACES
    )
    assert (
        button._category_for_button_type("Universal interface, 4 channels")
        == button.CATEGORY_INTERFACES
    )
    assert (
        button._category_for_button_type("Modular interface, 6 inputs")
        == button.CATEGORY_INTERFACES
    )
    assert (
        button._category_for_button_type("Interface for push buttons")
        == button.CATEGORY_INTERFACES
    )
    assert (
        button._category_for_button_type("Interface for switches")
        == button.CATEGORY_INTERFACES
    )


def test_rf_devices_route_to_remotes() -> None:
    button = _load_button_module()
    assert (
        button._category_for_button_type("Single RF-bus push button, 2 operation areas")
        == button.CATEGORY_REMOTES
    )
    assert (
        button._category_for_button_type("Double RF-bus push button, 4 operation areas")
        == button.CATEGORY_REMOTES
    )
    assert (
        button._category_for_button_type("Mini hand-held RF transmitter, 1 channel")
        == button.CATEGORY_REMOTES
    )
    assert (
        button._category_for_button_type(
            "Easywave hand-held RF transmitter, 52 operation points"
        )
        == button.CATEGORY_REMOTES
    )
    assert (
        button._category_for_button_type("RF868 mini transmitter, 4 channels")
        == button.CATEGORY_REMOTES
    )


def test_wall_buttons_route_to_wall_buttons() -> None:
    button = _load_button_module()
    assert (
        button._category_for_button_type("Bus push button, 4 control buttons")
        == button.CATEGORY_WALL_BUTTONS
    )
    assert (
        button._category_for_button_type(
            "Bus push button, 4 control buttons with IR receiver"
        )
        == button.CATEGORY_WALL_BUTTONS
    )
    assert (
        button._category_for_button_type(
            "Bus push button, 8 control buttons with eight feedback LEDs"
        )
        == button.CATEGORY_WALL_BUTTONS
    )
