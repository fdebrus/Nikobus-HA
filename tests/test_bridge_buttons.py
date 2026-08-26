"""Bridge action buttons: availability gating + .nkb import replay.

3.11.0 field report (issue #478 follow-up): the bridge buttons stayed
enabled with no visual feedback while a scan ran (inviting
double-triggers), and the "3. Import Names from .nkb" button always ran
its own hardcoded defaults instead of the settings last applied through
the options flow. The buttons now grey out (``available = False``)
whenever ``discovery_running`` is set, and the import button replays the
remembered options.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.nikobus.button import (
    NikobusImportNkbNamesButton,
    NikobusModuleScanButton,
    NikobusPcLinkInventoryButton,
)
from custom_components.nikobus.const import (
    CONF_NKB_IMPORT_CATEGORIES,
    CONF_NKB_IMPORT_OVERWRITE,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _coordinator(*, discovery_running=False, has_output_modules=True):
    coord = MagicMock()
    coord.discovery_running = discovery_running
    coord.has_known_output_modules = has_output_modules
    coord.config_entry.options = {}
    return coord


# --------------------------------------------------------------------------- #
# Availability: every bridge button greys out while a scan runs
# --------------------------------------------------------------------------- #
def test_all_bridge_buttons_unavailable_while_discovery_runs():
    coord = _coordinator(discovery_running=True)
    assert NikobusPcLinkInventoryButton(coord).available is False
    assert NikobusModuleScanButton(coord).available is False
    assert NikobusImportNkbNamesButton(coord).available is False


def test_all_bridge_buttons_available_when_idle():
    coord = _coordinator(discovery_running=False)
    assert NikobusPcLinkInventoryButton(coord).available is True
    assert NikobusModuleScanButton(coord).available is True
    assert NikobusImportNkbNamesButton(coord).available is True


def test_module_scan_button_still_requires_known_modules():
    """The pre-existing gate (nothing to scan before an inventory has
    populated storage) composes with the running gate."""
    coord = _coordinator(discovery_running=False, has_output_modules=False)
    assert NikobusModuleScanButton(coord).available is False


# --------------------------------------------------------------------------- #
# Import button: replays the last-used options-flow settings
# --------------------------------------------------------------------------- #
def test_import_button_defaults_to_everything_non_destructive():
    coord = _coordinator()
    coord.async_import_nkb_names = AsyncMock(
        return_value={"devices": 0, "entities": 0, "areas": 0, "scenes": 0}
    )
    _run(NikobusImportNkbNamesButton(coord).async_press())
    coord.async_import_nkb_names.assert_awaited_once_with(
        categories=None, overwrite=False
    )


def test_import_button_replays_remembered_options():
    coord = _coordinator()
    coord.config_entry.options = {
        CONF_NKB_IMPORT_CATEGORIES: ["device_names", "areas"],
        CONF_NKB_IMPORT_OVERWRITE: True,
    }
    coord.async_import_nkb_names = AsyncMock(
        return_value={"devices": 0, "entities": 0, "areas": 0, "scenes": 0}
    )
    _run(NikobusImportNkbNamesButton(coord).async_press())
    coord.async_import_nkb_names.assert_awaited_once_with(
        categories={"device_names", "areas"}, overwrite=True
    )


def test_import_button_ignores_unknown_stored_categories():
    """A stale/corrupt option value must not leak arbitrary strings into
    the import — only known categories pass the filter, and an
    empty/garbage list falls back to 'everything'."""
    coord = _coordinator()
    coord.config_entry.options = {
        CONF_NKB_IMPORT_CATEGORIES: ["device_names", "bogus"],
    }
    coord.async_import_nkb_names = AsyncMock(
        return_value={"devices": 0, "entities": 0, "areas": 0, "scenes": 0}
    )
    _run(NikobusImportNkbNamesButton(coord).async_press())
    coord.async_import_nkb_names.assert_awaited_once_with(
        categories={"device_names"}, overwrite=False
    )


# --------------------------------------------------------------------------- #
# Press-simulation entities are DIAGNOSTIC (3.13.0): tools, not room
# controls — keeps ~150 of them out of Controls sections and
# auto-dashboards while staying fully usable in automations.
# --------------------------------------------------------------------------- #
def test_press_entities_are_diagnostic():
    from custom_components.nikobus.binary_sensor import NikobusButtonBinarySensor
    from custom_components.nikobus.button import NikobusButtonEntity

    assert NikobusButtonEntity._attr_entity_category == "diagnostic"
    assert NikobusButtonBinarySensor._attr_entity_category == "diagnostic"
    # Press sensors additionally stay disabled by default.
    assert NikobusButtonBinarySensor._attr_entity_registry_enabled_default is False
