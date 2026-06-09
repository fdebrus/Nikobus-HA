"""Tests for ``async_migrate_entry`` (config-entry schema migration)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from custom_components.nikobus import async_migrate_entry
from custom_components.nikobus.const import CONFIG_ENTRY_VERSION


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAsyncMigrateEntry(unittest.TestCase):
    def _entry(self, version: int, minor_version: int = 0) -> MagicMock:
        entry = MagicMock()
        entry.version = version
        entry.minor_version = minor_version
        return entry

    def test_current_version_passes_through(self):
        self.assertTrue(
            _run(async_migrate_entry(MagicMock(), self._entry(CONFIG_ENTRY_VERSION)))
        )

    def test_future_version_refused(self):
        # Downgrade protection: an entry written by a newer release of
        # the integration must not be loaded (and silently mangled) by
        # an older one.
        self.assertFalse(
            _run(
                async_migrate_entry(
                    MagicMock(), self._entry(CONFIG_ENTRY_VERSION + 1)
                )
            )
        )
