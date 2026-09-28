"""Audio triggers belong to their module, not to the wall buttons.

A 05-205 is driven by virtual buttons no wall plate owns. Filing their
devices under the Wall buttons category put them next to real keypads
and, worse, left the module itself with no entity of its own — and Home
Assistant drops a config-entry device that ends up with none, which is
why a validating install reported "no device for 8334" while discovery
had read its whole link table (Nikobus-HA #310).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.nikobus.devices import register_wall_button_devices
from custom_components.nikobus.router import audio_trigger_naming

TRIGGER = {
    "type": "Audio Trigger",
    "model": "05-205",
    "description": "Zone 1 On",
    "audio_function": "M16 (On)",
    "audio_zone": 1,
    "audio_module_address": "8334",
    "operation_points": {"AUD": {"bus_address": "8083CF"}},
}

MODULE_DATA = {
    "audio_module": {
        "8334": {"description": "Audio link (8334)", "model": "05-205"}
    }
}


_DEFAULT = object()


def _register(phys, module_data=_DEFAULT, address="8083CF"):
    """Run the registration and return every device call it made."""
    dev_reg = MagicMock()
    dev_reg.async_get_device.return_value = MagicMock(id="PARENT")
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    if module_data is _DEFAULT:
        module_data = MODULE_DATA
    with patch(
        "custom_components.nikobus.devices.dr.async_get", return_value=dev_reg
    ):
        register_wall_button_devices(hass, entry, {address: phys}, module_data)
    return {
        next(iter(c.kwargs["identifiers"]))[1]: c.kwargs
        for c in dev_reg.async_get_or_create.call_args_list
    }


# --- what the naming helper recognises -----------------------------------


def test_an_audio_trigger_is_recognised_by_its_function_and_module():
    assert audio_trigger_naming(TRIGGER) == ("Zone 1 On", ("nikobus", "8334"))


def test_an_ordinary_button_is_not_an_audio_trigger():
    assert audio_trigger_naming({"type": "Wall Button"}) is None


def test_a_trigger_that_names_no_module_is_left_alone():
    # Stores written before library 0.39.1 carry no module address; the
    # trigger falls through to the ordinary wall-button path rather than
    # being parented to nothing.
    assert audio_trigger_naming({**TRIGGER, "audio_module_address": ""}) is None


# --- what gets registered -------------------------------------------------


def test_the_trigger_is_parented_under_its_audio_module():
    devices = _register(TRIGGER)
    assert devices["8083CF"]["name"] == "Zone 1 On"
    assert devices["8083CF"]["model"] == "05-205"
    # Not the Wall buttons category — the module.
    assert "category_wall_buttons" not in str(devices["8083CF"])


def test_the_module_is_registered_first_so_via_device_resolves():
    devices = _register(TRIGGER)
    assert devices["8334"]["name"] == "Audio link (8334)"
    assert devices["8334"]["model"] == "05-205"


def test_a_module_missing_from_the_store_still_gets_a_parent():
    devices = _register(TRIGGER, module_data={})
    assert devices["8334"]["name"] == "Audio Distribution (8334)"
    assert devices["8334"]["model"] == "05-205"


def test_an_imported_name_wins_over_the_generated_one():
    devices = _register({**TRIGGER, "nkb_name": "Keuken aan"})
    assert devices["8083CF"]["name"] == "Keuken aan"


def test_a_power_trigger_registers_like_any_other():
    power = {
        **TRIGGER,
        "description": "Audio Power",
        "audio_function": "M01 (Power)",
        "audio_zone": None,
        "audio_power": True,
    }
    devices = _register(power, address="8483CF")
    assert devices["8483CF"]["name"] == "Audio Power"
    assert "8334" in devices


def test_the_parent_module_is_registered_once_for_many_triggers():
    dev_reg = MagicMock()
    dev_reg.async_get_device.return_value = MagicMock(id="PARENT")
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    store = {
        f"{n:02X}83CF": {**TRIGGER, "description": f"Zone 1 trigger {n}"}
        for n in (0x00, 0x20, 0x40, 0x80)
    }
    with patch(
        "custom_components.nikobus.devices.dr.async_get", return_value=dev_reg
    ):
        register_wall_button_devices(hass, entry, store, MODULE_DATA)
    parents = [
        c
        for c in dev_reg.async_get_or_create.call_args_list
        if ("nikobus", "8334") in c.kwargs["identifiers"]
    ]
    assert len(parents) == 1
    assert len(dev_reg.async_get_or_create.call_args_list) == 5
