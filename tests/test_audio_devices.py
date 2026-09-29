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


# --- the entities on a trigger --------------------------------------------
#
# 3.21.0 shipped the triggers into the store and then lost every entity on
# them: the entity platforms parent an op point's device under the store
# entry's address, and an audio trigger's op point *is* the store entry,
# so its device was told to hang under itself. Home Assistant refuses
# that ("a device can not be its own via device") and drops the entity —
# 35 buttons and 35 sensors per setup on the validating install.


def _entity(cls, physical, op_point, parent_phys):
    from unittest.mock import MagicMock

    coordinator = MagicMock()
    coordinator.hass = None  # no registry: via_device_id stays unresolved
    return cls(coordinator, physical, "AUD", op_point, parent_phys=parent_phys)


def test_a_trigger_entity_hangs_under_the_audio_module_not_itself():
    from custom_components.nikobus.binary_sensor import NikobusButtonBinarySensor
    from custom_components.nikobus.button import NikobusButtonEntity

    for cls in (NikobusButtonEntity, NikobusButtonBinarySensor):
        entity = _entity(cls, "8083CF", {"bus_address": "8083CF"}, TRIGGER)
        assert entity._attr_device_info["identifiers"] == {("nikobus", "8083CF")}
        assert entity._via_device == ("nikobus", "8334")
        assert entity._attr_device_info["model"] == "Audio Trigger"


def test_a_wall_key_still_hangs_under_its_plate():
    from custom_components.nikobus.button import NikobusButtonEntity

    plate = {"type": "Bus push button, 4 control buttons", "model": "05-064"}
    entity = _entity(NikobusButtonEntity, "0D1C80", {"bus_address": "804E2C"}, plate)
    assert entity._attr_device_info["identifiers"] == {("nikobus", "804E2C")}
    assert entity._via_device == ("nikobus", "0D1C80")
    assert entity._attr_device_info["model"] == "Push Button"


def test_an_entry_that_is_its_own_op_point_never_parents_to_itself():
    """The guard is general: no marker, same address — no parent, not self."""
    from custom_components.nikobus.router import op_point_parent_device

    assert op_point_parent_device("ABCDEF", "abcdef", {"type": "Whatever"}) is None
    assert op_point_parent_device("ABCDEF", "abcdef", None) is None
    assert op_point_parent_device("0D1C80", "804E2C", None) == ("nikobus", "0D1C80")
    assert op_point_parent_device("8083CF", "8083CF", TRIGGER) == ("nikobus", "8334")
