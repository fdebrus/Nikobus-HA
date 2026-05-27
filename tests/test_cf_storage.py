"""Regression tests for ``NikobusCFStorage``.

2.12.0: the library classifies ``38 41 XX`` and ``38 80 XX`` bus
addresses as CF activation broadcasts and exposes them on
``NikobusDiscovery.discovered_cf_broadcasts``. The coordinator
mirrors that dict into a HA Store so scene entities survive HA
restarts without re-running discovery. These tests pin the storage
load / save contract.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from custom_components.nikobus.nkbstorage import NikobusCFStorage


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestNikobusCFStorage(unittest.TestCase):
    def test_initial_data_shape(self):
        """A freshly-constructed storage has the canonical empty shape."""
        store = NikobusCFStorage(MagicMock())
        self.assertEqual(store.data, {"nikobus_cf": {}})
        self.assertTrue(store.is_empty)

    def test_load_returns_canonical_shape_when_disk_is_empty(self):
        """The conftest stub's ``async_load`` returns ``None`` — the
        storage must coerce that to the canonical empty shape rather
        than leaving ``self._data`` as ``None``."""
        store = NikobusCFStorage(MagicMock())
        result = _run(store.async_load())
        self.assertEqual(result, {"nikobus_cf": {}})
        self.assertTrue(store.is_empty)

    def test_data_round_trip_through_save(self):
        """Mutating ``data`` and calling save preserves the dict
        in-memory (and the conftest no-op store accepts the save)."""
        store = NikobusCFStorage(MagicMock())
        _run(store.async_load())
        store.data["nikobus_cf"]["3880CA"] = {
            "bus_address": "3880CA",
            "pattern": "roller_pair",
            "outputs": [
                {"module_address": "8CF5", "channel": 6, "mode": "M02"},
                {"module_address": "8CF5", "channel": 6, "mode": "M03"},
            ],
        }
        _run(store.async_save())
        self.assertFalse(store.is_empty)
        self.assertIn("3880CA", store.data["nikobus_cf"])
        self.assertEqual(
            len(store.data["nikobus_cf"]["3880CA"]["outputs"]), 2
        )

    def test_load_accepts_persisted_shape(self):
        """When the on-disk store holds a populated ``nikobus_cf`` dict,
        load surfaces it verbatim."""
        store = NikobusCFStorage(MagicMock())
        # Inject the loaded shape directly into the underlying stub.
        persisted = {
            "nikobus_cf": {
                "384102": {
                    "bus_address": "384102",
                    "pattern": "switch_pair",
                    "outputs": [
                        {"module_address": "81F6", "channel": 1, "mode": "M03"},
                    ],
                }
            }
        }
        store._store._data = persisted  # type: ignore[attr-defined]

        # Stub the conftest Store's async_load to return our persisted
        # data this one time.
        original_load = store._store.async_load

        async def fake_load():
            return persisted

        store._store.async_load = fake_load  # type: ignore[method-assign]
        try:
            result = _run(store.async_load())
            self.assertEqual(result, persisted)
            self.assertEqual(
                store.data["nikobus_cf"]["384102"]["pattern"], "switch_pair"
            )
        finally:
            store._store.async_load = original_load  # type: ignore[method-assign]

    def test_load_rejects_malformed_disk_shape(self):
        """If the on-disk payload is corrupt (missing nikobus_cf key, or
        the wrong type), fall back to the canonical empty shape rather
        than propagate the garbage."""
        store = NikobusCFStorage(MagicMock())

        async def fake_load():
            return {"unrelated": "junk"}

        store._store.async_load = fake_load  # type: ignore[method-assign]
        result = _run(store.async_load())
        self.assertEqual(result, {"nikobus_cf": {}})


if __name__ == "__main__":
    unittest.main()
