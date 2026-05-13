"""Repair flows for the Nikobus integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import DOMAIN, ISSUE_LEGACY_UNDECODED_BUTTONS
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


class LegacyUndecodedButtonsRepairFlow(RepairsFlow):
    """Let the user choose which legacy_undecoded buttons to remove.

    Surfaced after Stage-2 scan-all when one or more buttons have no
    decoded ``linked_modules``. We cannot programmatically tell apart:

      * Wall buttons intentionally unwired in the PC-Link project and
        used solely as HA automation triggers (KEEP).
      * Residue from a previous owner / removed hardware (PURGE).

    The flow renders the candidate set as a markdown table and asks the
    user to pick which addresses to remove. Recoverable: a re-run of
    PC-Link inventory re-adds anything currently in the project.
    """

    def __init__(self, entry_id: str, addresses: list[str]) -> None:
        """Store the entry id and the candidate addresses surfaced by the issue."""
        self._entry_id = entry_id
        self._candidates = [str(a).upper() for a in addresses]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the candidate list with a multi-select."""
        coordinator = _coordinator(self.hass, self._entry_id)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
        # Filter candidates to those still in the store AND still
        # ``legacy_undecoded`` — the user may have manually purged some
        # via the service in the meantime, or another scan may have
        # decoded their links.
        still_legacy = [
            addr
            for addr in self._candidates
            if isinstance(buttons.get(addr), dict)
            and buttons[addr].get("status") == "legacy_undecoded"
        ]
        if not still_legacy:
            return self.async_abort(reason="no_candidates")

        if user_input is not None:
            selected = [
                str(addr).upper() for addr in (user_input.get("addresses") or [])
            ]
            if selected:
                await coordinator.purge_inventory_addresses(selected)
            return self.async_create_entry(title="", data={})

        options = [
            SelectOptionDict(
                value=addr, label=self._format_label(addr, buttons[addr])
            )
            for addr in still_legacy
        ]
        schema = vol.Schema(
            {
                vol.Optional("addresses", default=[]): SelectSelector(
                    SelectSelectorConfig(
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        options=options,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "count": str(len(still_legacy)),
                "candidates": self._render_table(still_legacy, buttons),
            },
        )

    @staticmethod
    def _format_label(address: str, phys: dict[str, Any]) -> str:
        """Compact one-line label for the multi-select dropdown entry."""
        btype = phys.get("type") or "Unknown type"
        model = phys.get("model") or ""
        description = phys.get("description") or ""
        # Strip the auto-appended ``#N<addr>`` so labels read cleanly.
        if description.endswith(f"#N{address}"):
            description = description[: -len(f"#N{address}")].rstrip()
        bits = [address, btype]
        if model and model.lower() != "unknown":
            bits.append(model)
        if description and description not in bits:
            bits.append(description)
        return " — ".join(bits)

    @staticmethod
    def _render_table(addresses: list[str], buttons: dict[str, Any]) -> str:
        """Markdown table of candidates injected into the form description."""
        rows = ["| Address | Type | Model | Description |",
                "|---|---|---|---|"]
        for addr in addresses:
            phys = buttons.get(addr) or {}
            btype = phys.get("type") or "Unknown"
            model = phys.get("model") or "—"
            description = phys.get("description") or ""
            # Strip the auto-suffix and any pipe chars that would break
            # the table layout.
            if description.endswith(f"#N{addr}"):
                description = description[: -len(f"#N{addr}")].rstrip()
            description = description.replace("|", "/") or "—"
            rows.append(f"| `{addr}` | {btype} | {model} | {description} |")
        return "\n".join(rows)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the appropriate repair flow for the given issue id."""
    entry_id = (data or {}).get("entry_id", "")
    if issue_id.startswith(ISSUE_LEGACY_UNDECODED_BUTTONS):
        addresses = (data or {}).get("addresses") or []
        return LegacyUndecodedButtonsRepairFlow(entry_id, addresses)
    return NoButtonsConfiguredRepairFlow(entry_id)


def _coordinator(hass: HomeAssistant, entry_id: str) -> NikobusDataCoordinator | None:
    """Return the loaded coordinator for the given entry id, if any."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data
