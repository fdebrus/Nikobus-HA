"""Repair flows for the Nikobus integration."""

from __future__ import annotations

from typing import Any, ClassVar

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

from .const import (
    BUTTON_LEGACY_KEEP_KEY,
    ISSUE_LEGACY_UNDECODED_BUTTONS,
    LEGACY_BUTTON_STATUSES,
)
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
    """Let the user choose which legacy buttons to remove.

    Surfaced after Stage-2 scan-all when one or more buttons land in
    a legacy bucket:

      * ``legacy_undecoded`` — no decoded links across any op-point.
        Either intentionally unwired (HA-trigger pattern, KEEP) or
        residue with no surviving link records (PURGE).
      * ``legacy_orphan`` — has decoded links but either every linked
        module was just evicted, or (with nikobus-connect 0.5.22+)
        every output record is sourced from PC-Link / PC-Logic
        registry memory with no PC-Logic in the install (i.e.,
        residue programming from a previous owner that DIN-button
        re-pairing didn't clear).

    The flow renders the combined candidate set as a markdown table
    and asks the user to pick which addresses to remove. Recoverable:
    a re-run of PC-Link inventory + Stage-2 re-adds anything currently
    in the project.
    """

    def __init__(self, entry_id: str, addresses: list[str]) -> None:
        """Store the entry id and the addresses the issue was raised for."""
        self._entry_id = entry_id
        self._candidates = [str(a).upper() for a in addresses]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Open the review form.

        Home Assistant passes ``{"issue_id": ...}`` to a repair flow's
        first step as its input, a compatibility fallback, so ``init``
        never sees ``None``. Reading that as a submission closed the
        issue at once with nothing chosen (#540). The form lives in its
        own step, reached here with no input.
        """
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the candidates with a remove list and a keep list."""
        coordinator = _coordinator(self.hass, self._entry_id)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
        # Every button still in a legacy bucket, read from the store now:
        # the user may have purged some via the service, another scan may
        # have decoded their links, and buttons kept in an earlier review
        # are listed too (their keep pre-ticked) so a keep can be undone.
        candidates = sorted(
            str(addr).upper()
            for addr, phys in buttons.items()
            if isinstance(phys, dict) and phys.get("status") in LEGACY_BUTTON_STATUSES
        )
        if not candidates:
            return self.async_abort(reason="no_candidates")

        errors: dict[str, str] = {}
        remove: list[str] = []
        keep: list[str] = [
            addr for addr in candidates if buttons[addr].get(BUTTON_LEGACY_KEEP_KEY)
        ]
        if user_input is not None:
            remove = _chosen(user_input.get("addresses"), candidates)
            keep = _chosen(user_input.get("keep"), candidates)
            if set(remove) & set(keep):
                errors["base"] = "remove_and_keep"
            else:
                await self._apply(coordinator, buttons, candidates, remove, keep)
                return self.async_create_entry(title="", data={})

        options = [
            SelectOptionDict(value=addr, label=self._format_label(addr, buttons[addr]))
            for addr in candidates
        ]

        def _list() -> SelectSelector:
            return SelectSelector(
                SelectSelectorConfig(
                    multiple=True, mode=SelectSelectorMode.LIST, options=options
                )
            )

        schema = vol.Schema(
            {
                vol.Optional("addresses", default=remove): _list(),
                vol.Optional("keep", default=keep): _list(),
            }
        )
        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count": str(len(candidates)),
                "candidates": self._render_table(candidates, buttons),
            },
        )

    @staticmethod
    async def _apply(
        coordinator: NikobusDataCoordinator,
        buttons: dict[str, Any],
        candidates: list[str],
        remove: list[str],
        keep: list[str],
    ) -> None:
        """Record the keeps, then purge the removals.

        A candidate in neither list loses any earlier keep: unticking a
        keep is how it is undone.
        """
        kept = set(keep)
        changed = False
        for addr in candidates:
            if addr in remove:
                continue
            phys = buttons[addr]
            if addr in kept and not phys.get(BUTTON_LEGACY_KEEP_KEY):
                phys[BUTTON_LEGACY_KEEP_KEY] = True
                changed = True
            elif addr not in kept and BUTTON_LEGACY_KEEP_KEY in phys:
                phys.pop(BUTTON_LEGACY_KEEP_KEY, None)
                changed = True
        if remove:
            # Saves the button store as well, keeps included.
            await coordinator.purge_inventory_addresses(remove)
        elif changed:
            await coordinator.button_storage.async_save()

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

    _REASON_LABELS: ClassVar[dict[str, str]] = {
        "legacy_undecoded": "no decoded links",
        "legacy_orphan": "residue / stale links",
    }

    @classmethod
    def _render_table(cls, addresses: list[str], buttons: dict[str, Any]) -> str:
        """Markdown table of candidates injected into the form description."""
        rows = ["| Address | Type | Model | Reason | Description |",
                "|---|---|---|---|---|"]
        for addr in addresses:
            phys = buttons.get(addr) or {}
            btype = phys.get("type") or "Unknown"
            model = phys.get("model") or "—"
            reason = cls._REASON_LABELS.get(phys.get("status") or "", "—")
            description = phys.get("description") or ""
            # Strip the auto-suffix and any pipe chars that would break
            # the table layout.
            if description.endswith(f"#N{addr}"):
                description = description[: -len(f"#N{addr}")].rstrip()
            description = description.replace("|", "/") or "—"
            rows.append(
                f"| `{addr}` | {btype} | {model} | {reason} | {description} |"
            )
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


def _chosen(raw: Any, candidates: list[str]) -> list[str]:
    """The submitted addresses that are candidates, upper-cased, in order."""
    picked = {str(addr).upper() for addr in (raw or [])} if isinstance(raw, list) else set()
    return [addr for addr in candidates if addr in picked]


def _coordinator(hass: HomeAssistant, entry_id: str) -> NikobusDataCoordinator | None:
    """Return the loaded coordinator for the given entry id, if any."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data
