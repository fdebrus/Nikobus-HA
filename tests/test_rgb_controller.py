"""The 340-00112 RGB controller is visible, and nothing more.

Device type 0x46, catalogued in nikobus-connect 0.40.0 from an install's
project file (#519). Nobody has read the module's registers, so how its
output is driven is unknown — it gets a device so the install shows what
is on the bus, and no entities until there is something real to build
them from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.nikobus.devices import register_opaque_module_devices
from custom_components.nikobus.router import OPAQUE_MODULE_TYPES, build_routing

MODULE_DATA = {
    "rgb_module": {
        "801D": {
            "module_type": "rgb_module",
            "description": "rgb_module_1",
            "model": "340-00112",
        }
    }
}


def _register(module_data):
    dev_reg = MagicMock()
    dev_reg.async_get_device.return_value = MagicMock(id="PARENT")
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    with patch(
        "custom_components.nikobus.devices.dr.async_get", return_value=dev_reg
    ):
        register_opaque_module_devices(hass, entry, module_data)
    return {
        next(iter(c.kwargs["identifiers"]))[1]: c.kwargs
        for c in dev_reg.async_get_or_create.call_args_list
    }


def test_the_controller_is_an_opaque_module():
    assert "rgb_module" in OPAQUE_MODULE_TYPES


def test_it_gets_a_device_of_its_own():
    devices = _register(MODULE_DATA)
    assert devices["801D"]["name"] == "rgb_module_1"
    assert devices["801D"]["model"] == "340-00112"


def test_the_device_survives_a_module_record_with_no_model():
    bare = {"rgb_module": {"801D": {"module_type": "rgb_module"}}}
    devices = _register(bare)
    assert devices["801D"]["name"] == "rgb_module (801D)"
    assert devices["801D"]["model"] == "rgb_module"


def test_no_channel_entities_are_built_for_it():
    """The router skips an opaque module outright, so nothing tries to
    switch or dim a load we cannot address — not even if a stale store
    still carries channels for it."""
    stale = {
        "rgb_module": {
            "801D": {
                "module_type": "rgb_module",
                "description": "rgb_module_1",
                "model": "340-00112",
                "channels": [{"description": "output_1"}],
            }
        }
    }
    for store in (MODULE_DATA, stale):
        assert all(not specs for specs in build_routing(store).values())
