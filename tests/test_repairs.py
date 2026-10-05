"""Tests for the legacy-undecoded repair: its label/table helpers, and
the flow's steps driven the way Home Assistant drives them (#540)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState

from custom_components.nikobus.const import BUTTON_LEGACY_KEEP_KEY
from custom_components.nikobus.repairs import LegacyUndecodedButtonsRepairFlow as RF


class TestFormatLabel(unittest.TestCase):
    def test_full_label_strips_n_suffix(self):
        label = RF._format_label(
            "0A0908",
            {
                "type": "Bus push button, 2 control buttons",
                "model": "05-060",
                "description": "Kitchen light #N0A0908",
            },
        )
        self.assertEqual(
            label, "0A0908 — Bus push button, 2 control buttons — 05-060 — Kitchen light"
        )

    def test_unknown_model_omitted(self):
        self.assertEqual(
            RF._format_label("AAAA", {"type": "Wall", "model": "Unknown"}),
            "AAAA — Wall",
        )

    def test_empty_phys_defaults(self):
        self.assertEqual(RF._format_label("AAAA", {}), "AAAA — Unknown type")

    def test_description_equal_to_type_not_duplicated(self):
        label = RF._format_label(
            "BBBB", {"type": "Foo", "description": "Foo #NBBBB"}
        )
        self.assertEqual(label, "BBBB — Foo")


class TestRenderTable(unittest.TestCase):
    def test_header_and_row(self):
        out = RF._render_table(
            ["0A0908"],
            {
                "0A0908": {
                    "type": "Bus push button",
                    "model": "05-060",
                    "status": "legacy_undecoded",
                    "description": "Foo #N0A0908",
                }
            },
        )
        lines = out.splitlines()
        self.assertEqual(lines[0], "| Address | Type | Model | Reason | Description |")
        self.assertEqual(
            lines[2], "| `0A0908` | Bus push button | 05-060 | no decoded links | Foo |"
        )

    def test_orphan_reason_and_pipe_sanitized(self):
        out = RF._render_table(
            ["X"],
            {"X": {"type": "T", "status": "legacy_orphan", "description": "a|b"}},
        )
        row = out.splitlines()[2]
        self.assertIn("residue / stale links", row)
        self.assertIn("a/b", row)  # pipe replaced so it doesn't break the table

    def test_missing_address_uses_defaults(self):
        out = RF._render_table(["ZZZZ"], {})
        self.assertEqual(out.splitlines()[2], "| `ZZZZ` | Unknown | — | — | — |")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# The flow itself (#540)
# ---------------------------------------------------------------------------


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _flow(buttons, *, loaded=True):
    """A flow wired to a loaded entry whose store holds ``buttons``."""
    coordinator = MagicMock()
    coordinator.dict_button_data = {"nikobus_button": buttons}
    coordinator.purge_inventory_addresses = AsyncMock()
    coordinator.button_storage.async_save = AsyncMock()
    entry = MagicMock()
    entry.state = ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED
    entry.runtime_data = coordinator

    flow = RF("E1", sorted(buttons))
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_entry.return_value = entry
    flow.async_show_form = lambda *, step_id, data_schema=None, errors=None, **kw: {
        "type": "form", "step_id": step_id, "data_schema": data_schema,
        "errors": errors or {}, "placeholders": kw.get("description_placeholders"),
    }
    flow.async_create_entry = lambda *, title=None, data=None, **kw: {"type": "create_entry"}
    flow.async_abort = lambda *, reason: {"type": "abort", "reason": reason}
    return flow, coordinator


def _store():
    return {
        "AAAAAA": {"status": "legacy_undecoded", "type": "Switch interface"},
        "BBBBBB": {"status": "legacy_orphan", "type": "Bus push button"},
        "CCCCCC": {"status": "legacy_undecoded", BUTTON_LEGACY_KEEP_KEY: True},
        "DDDDDD": {"status": "active"},
    }


def _defaults(result):
    return {str(key): key.default() for key in result["data_schema"]}


class TestFlowOpens(unittest.TestCase):
    def test_the_issue_id_core_passes_on_open_shows_the_form(self):
        """Home Assistant starts a repair flow with ``{"issue_id": ...}`` as
        the first step's input. That must open the form, not submit it."""
        flow, coordinator = _flow(_store())
        result = _run(flow.async_step_init({"issue_id": "legacy_undecoded_buttons_E1"}))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "select")
        coordinator.purge_inventory_addresses.assert_not_awaited()
        coordinator.button_storage.async_save.assert_not_awaited()

    def test_the_form_lists_every_legacy_button_and_pre_ticks_the_kept_ones(self):
        flow, _ = _flow(_store())
        result = _run(flow.async_step_init(None))
        self.assertEqual(result["placeholders"]["count"], "3")
        self.assertEqual(_defaults(result), {"addresses": [], "keep": ["CCCCCC"]})

    def test_no_legacy_button_left_aborts(self):
        flow, _ = _flow({"DDDDDD": {"status": "active"}})
        self.assertEqual(_run(flow.async_step_init(None))["reason"], "no_candidates")

    def test_an_unloaded_entry_aborts(self):
        flow, _ = _flow(_store(), loaded=False)
        self.assertEqual(_run(flow.async_step_init(None))["reason"], "not_loaded")


class TestFlowSubmits(unittest.TestCase):
    def test_remove_purges_exactly_the_ticked_buttons(self):
        flow, coordinator = _flow(_store())
        result = _run(flow.async_step_select({"addresses": ["aaaaaa", "BBBBBB"], "keep": ["CCCCCC"]}))
        self.assertEqual(result["type"], "create_entry")
        coordinator.purge_inventory_addresses.assert_awaited_once_with(["AAAAAA", "BBBBBB"])

    def test_keep_marks_the_buttons_and_saves(self):
        buttons = _store()
        flow, coordinator = _flow(buttons)
        _run(flow.async_step_select({"addresses": [], "keep": ["AAAAAA", "CCCCCC"]}))
        self.assertTrue(buttons["AAAAAA"][BUTTON_LEGACY_KEEP_KEY])
        self.assertTrue(buttons["CCCCCC"][BUTTON_LEGACY_KEEP_KEY])
        coordinator.purge_inventory_addresses.assert_not_awaited()
        coordinator.button_storage.async_save.assert_awaited_once()

    def test_unticking_a_keep_undoes_it(self):
        buttons = _store()
        flow, coordinator = _flow(buttons)
        _run(flow.async_step_select({"addresses": [], "keep": []}))
        self.assertNotIn(BUTTON_LEGACY_KEEP_KEY, buttons["CCCCCC"])
        coordinator.button_storage.async_save.assert_awaited_once()

    def test_nothing_chosen_changes_nothing(self):
        buttons = _store()
        flow, coordinator = _flow(buttons)
        result = _run(flow.async_step_select({"addresses": [], "keep": ["CCCCCC"]}))
        self.assertEqual(result["type"], "create_entry")
        coordinator.purge_inventory_addresses.assert_not_awaited()
        coordinator.button_storage.async_save.assert_not_awaited()

    def test_a_button_in_both_lists_is_refused_and_nothing_happens(self):
        buttons = _store()
        flow, coordinator = _flow(buttons)
        result = _run(flow.async_step_select({"addresses": ["AAAAAA"], "keep": ["AAAAAA"]}))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "remove_and_keep"})
        self.assertEqual(_defaults(result), {"addresses": ["AAAAAA"], "keep": ["AAAAAA"]})
        coordinator.purge_inventory_addresses.assert_not_awaited()
        self.assertNotIn(BUTTON_LEGACY_KEEP_KEY, buttons["AAAAAA"])

    def test_addresses_that_are_not_candidates_are_ignored(self):
        flow, coordinator = _flow(_store())
        _run(flow.async_step_select({"addresses": ["DDDDDD", "123456"], "keep": ["CCCCCC"]}))
        coordinator.purge_inventory_addresses.assert_not_awaited()
