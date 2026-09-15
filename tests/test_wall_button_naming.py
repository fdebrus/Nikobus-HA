"""Wall-button device naming: registry index, .nkb name precedence.

``register_wall_button_devices`` builds the integration-provided device
name re-asserted on every restart. Precedence:

1. ``nkb_name`` — persisted by the .nkb import (3.12.0);
2. ``component_number`` + generated name — the Niko software's index
   read from the PC-Link registry (library 0.33.0), for installs
   without an .nkb;
3. plain generated name.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.nikobus.button import register_wall_button_devices


def _register(phys):
    dev_reg = MagicMock()
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    with patch(
        "custom_components.nikobus.devices.dr.async_get", return_value=dev_reg
    ):
        register_wall_button_devices(hass, entry, {"1843B4": phys})
    calls = [
        c
        for c in dev_reg.async_get_or_create.call_args_list
        if ("nikobus", "1843B4") in c.kwargs.get("identifiers", set())
    ]
    assert len(calls) == 1
    return calls[0].kwargs["name"]


def test_plain_generated_name_without_number():
    name = _register(
        {"type": "Bus push button, 4 control buttons", "model": "05-064"}
    )
    assert name == "Bus push button, 4 control buttons (1843B4)"


def test_registry_component_number_prefixes_generated_name():
    name = _register(
        {
            "type": "Bus push button, 4 control buttons",
            "model": "05-064",
            "component_number": 7,
        }
    )
    assert name == "7: Bus push button, 4 control buttons (1843B4)"


def test_nkb_name_wins_over_registry_number():
    name = _register(
        {
            "type": "Bus push button, 4 control buttons",
            "model": "05-064",
            "component_number": 7,
            "nkb_name": "7: Porte buanderie (Buanderie)",
        }
    )
    assert name == "7: Porte buanderie (Buanderie)"


def test_calendar_channel_is_named_after_the_channel_under_the_bridge():
    """A link to a PC-Link calendar channel becomes a device named as the
    Nikobus software names the channel, parented under the bridge, and
    gets no press entity (what the PC-Link emits for it is unknown)."""
    from custom_components.nikobus.button import _iter_button_entities

    phys = {
        "description": "PC-Link calendar channel CH001A",
        "type": "PC-Link Calendar Channel",
        "model": "05-200",
        "calendar_channel": "CH001A",
        "operation_points": {"CAL": {"bus_address": "E00320", "description": "Calendar channel CH001A (PC-Link)"}},
    }
    dev_reg = MagicMock()
    hass, entry = MagicMock(), MagicMock()
    entry.entry_id = "E1"
    with patch("custom_components.nikobus.devices.dr.async_get", return_value=dev_reg):
        register_wall_button_devices(hass, entry, {"E00320": phys})
    calls = [
        c for c in dev_reg.async_get_or_create.call_args_list
        if ("nikobus", "E00320") in c.kwargs.get("identifiers", set())
    ]
    assert len(calls) == 1
    assert calls[0].kwargs["name"] == "PC-Link calendar CH001A"
    assert calls[0].kwargs["model"] == "PC-Link calendar channel"
    dev_reg.async_get_device_by_identifier.assert_any_call(("nikobus", "nikobus_hub"), "E1")
    assert list(_iter_button_entities(MagicMock(), {"E00320": phys})) == []
