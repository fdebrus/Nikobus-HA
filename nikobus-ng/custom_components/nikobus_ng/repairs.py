"""The repairs a person can settle from the dialog."""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_FEEDBACK_MODULE,
    DOMAIN,
    ISSUE_FEEDBACK_PORT,
    ISSUE_NOT_ANSWERING,
)
from .hub import NikobusHub
from .maintenance import ISSUE_REPROGRAMMED


def _hub(hass: HomeAssistant, entry_id: str) -> NikobusHub | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry.runtime_data if entry and entry.state is ConfigEntryState.LOADED else None


class DiscoverRepairFlow(RepairsFlow):
    """A module was reprogrammed: read its links again."""

    def __init__(self, entry_id: str, address: str) -> None:
        self._entry_id = entry_id
        self._address = address

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Ask first: reading a module's links takes the bus for a while."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Start reading the module's links; the end of that clears the issue."""
        if user_input is None:
            return self.async_show_form(step_id="confirm")
        if (hub := _hub(self.hass, self._entry_id)) is None:
            return self.async_abort(reason="not_loaded")
        try:
            hub.discovery.start(self._address or None)
        except HomeAssistantError:
            return self.async_abort(reason="bus_taken")
        return self.async_create_entry(data={})


class FeedbackPortRepairFlow(RepairsFlow):
    """The cable is on the Feedback Module's own port: stop following its reads."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Ask first: the option changes how state is kept up to date."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Turn the Feedback Module option off and reload."""
        if user_input is None:
            return self.async_show_form(step_id="confirm")
        if (entry := self.hass.config_entries.async_get_entry(self._entry_id)) is None:
            return self.async_abort(reason="not_loaded")
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_FEEDBACK_MODULE: False}
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data={})


class RemoveModuleRepairFlow(RepairsFlow):
    """A module does not answer any more: remove it and its entities."""

    def __init__(self, entry_id: str, address: str) -> None:
        self._entry_id = entry_id
        self._address = address

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Ask first: the entities go with the module."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Forget the module and delete its device."""
        if (hub := _hub(self.hass, self._entry_id)) is None:
            return self.async_abort(reason="not_loaded")
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                description_placeholders={"module": hub.inventory.module_label(self._address)},
            )
        await hub.async_forget(self._address)
        devices = dr.async_get(self.hass)
        if device := devices.async_get_device_by_identifier((DOMAIN, self._address), self._entry_id):
            devices.async_remove_device(device.id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """The flow of one of the issues that can be fixed here."""
    entry_id = str((data or {}).get("entry_id") or "")
    if issue_id.startswith(ISSUE_REPROGRAMMED):
        return DiscoverRepairFlow(entry_id, str((data or {}).get("address") or ""))
    if issue_id.startswith(ISSUE_FEEDBACK_PORT):
        return FeedbackPortRepairFlow(entry_id)
    if issue_id.startswith(ISSUE_NOT_ANSWERING):
        return RemoveModuleRepairFlow(entry_id, str((data or {}).get("address") or ""))
    raise ValueError(f"unknown issue {issue_id}")
