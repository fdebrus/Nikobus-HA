"""Repair flows for the Nikobus integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator


class NoButtonsConfiguredRepairFlow(RepairsFlow):
    """Offer to run PC-Link inventory discovery to populate the button config."""

    def __init__(self, entry_id: str) -> None:
        """Store the entry that owns this issue."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Show a confirm dialog for the discovery action."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Run the PC-Link inventory discovery on confirmation."""
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=None)

        coordinator = _coordinator(self.hass, self._entry_id)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        await coordinator.start_pc_link_inventory()
        return self.async_create_entry(title="", data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the appropriate repair flow for the given issue id."""
    entry_id = (data or {}).get("entry_id", "")
    return NoButtonsConfiguredRepairFlow(entry_id)


def _coordinator(hass: HomeAssistant, entry_id: str) -> NikobusDataCoordinator | None:
    """Return the loaded coordinator for the given entry id, if any."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data
