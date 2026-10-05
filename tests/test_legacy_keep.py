"""A button kept in the legacy review is not flagged again (#540)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from custom_components.nikobus.const import BUTTON_LEGACY_KEEP_KEY
from custom_components.nikobus.coordinator import NikobusDataCoordinator


def _coord():
    coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "E1"
    return coord


class TestSurfaceSkipsKept(unittest.TestCase):
    def _surface(self, buttons):
        created, deleted = [], []
        with patch("custom_components.nikobus.discovery_mixin.ir.async_create_issue",
                   side_effect=lambda *a, **k: created.append(k)), \
             patch("custom_components.nikobus.discovery_mixin.ir.async_delete_issue",
                   side_effect=lambda *a, **k: deleted.append(a)):
            _coord()._surface_legacy_undecoded_buttons(buttons)
        return created, deleted

    def test_kept_buttons_are_left_out_of_the_issue(self):
        created, _ = self._surface({
            "AAAAAA": {"status": "legacy_undecoded"},
            "CCCCCC": {"status": "legacy_undecoded", BUTTON_LEGACY_KEEP_KEY: True},
        })
        self.assertEqual(created[0]["data"]["addresses"], ["AAAAAA"])
        self.assertEqual(created[0]["translation_placeholders"], {"count": "1"})

    def test_only_kept_buttons_left_clears_the_issue(self):
        created, deleted = self._surface({
            "CCCCCC": {"status": "legacy_orphan", BUTTON_LEGACY_KEEP_KEY: True},
        })
        self.assertEqual(created, [])
        self.assertEqual(len(deleted), 1)
