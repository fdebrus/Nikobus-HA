"""Tests for the pure label/table helpers in the legacy-undecoded repair.

The flow steps need HA's RepairsFlow machinery; these cover the
static/class string builders that render the candidate list.
"""

from __future__ import annotations

import unittest

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
