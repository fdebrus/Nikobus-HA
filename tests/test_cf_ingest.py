"""Regression tests for ``coordinator._ingest_cf_broadcasts``.

2.12.0: after each discovery completes, the coordinator pulls the
library's classified CF activation broadcasts off
``NikobusDiscovery.discovered_cf_broadcasts`` and persists them to
``cf_storage``. These tests pin the conversion / dedup / persistence
contract without exercising the full discovery state machine.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.nikobus.coordinator import NikobusDataCoordinator


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _stub_cf_member(module_address, channel, mode, t1=None, t2=None):
    """Mimic the library's ``CFOutputMember`` dataclass shape using a
    plain SimpleNamespace — the coordinator only reads attributes."""
    m = MagicMock()
    m.module_address = module_address
    m.channel = channel
    m.mode = mode
    m.t1 = t1
    m.t2 = t2
    return m


def _stub_cf_broadcast(bus_address, pattern, outputs):
    cf = MagicMock()
    cf.bus_address = bus_address
    cf.pattern = pattern
    cf.outputs = outputs
    return cf


def _make_coord_with_cf_storage(broadcasts):
    """Build a coordinator-under-test with just enough wired to exercise
    ``_ingest_cf_broadcasts``: a fake ``nikobus_discovery`` exposing
    ``discovered_cf_broadcasts`` and a fake ``cf_storage`` that records
    saves. Everything else is left as MagicMock."""
    coord = MagicMock(spec=NikobusDataCoordinator)
    coord.nikobus_discovery = MagicMock()
    coord.nikobus_discovery.discovered_cf_broadcasts = broadcasts
    coord.cf_storage = MagicMock()
    coord.cf_storage.data = {"nikobus_cf": {}}
    coord.cf_storage.async_save = AsyncMock()
    coord._ingest_cf_broadcasts = (
        lambda _self=coord: NikobusDataCoordinator._ingest_cf_broadcasts(_self)
    )
    return coord


class TestIngestCFBroadcasts(unittest.TestCase):
    def test_roller_pair_with_two_members_lands_in_storage(self):
        coord = _make_coord_with_cf_storage(
            {
                "3880CA": _stub_cf_broadcast(
                    "3880CA",
                    "roller_pair",
                    [
                        _stub_cf_member("8CF5", 6, "M02", t1="30 s"),
                        _stub_cf_member("8CF5", 6, "M03", t1="30 s"),
                    ],
                )
            }
        )

        _run(coord._ingest_cf_broadcasts())

        stored = coord.cf_storage.data["nikobus_cf"]
        self.assertIn("3880CA", stored)
        self.assertEqual(stored["3880CA"]["pattern"], "roller_pair")
        self.assertEqual(len(stored["3880CA"]["outputs"]), 2)
        modes = {o["mode"] for o in stored["3880CA"]["outputs"]}
        self.assertEqual(modes, {"M02", "M03"})
        # Save was actually issued.
        coord.cf_storage.async_save.assert_awaited_once()

    def test_switch_pair_alles_uit_spray_lands_full(self):
        coord = _make_coord_with_cf_storage(
            {
                "384102": _stub_cf_broadcast(
                    "384102",
                    "switch_pair",
                    [
                        _stub_cf_member("81F6", i, "M03")
                        for i in range(1, 13)
                    ],
                )
            }
        )

        _run(coord._ingest_cf_broadcasts())

        stored = coord.cf_storage.data["nikobus_cf"]["384102"]
        self.assertEqual(stored["pattern"], "switch_pair")
        self.assertEqual(len(stored["outputs"]), 12)
        # Every entry is a plain JSON-safe dict.
        for o in stored["outputs"]:
            self.assertIsInstance(o, dict)
            self.assertIn("module_address", o)
            self.assertIn("channel", o)
            self.assertIn("mode", o)

    def test_module_address_normalised_to_uppercase(self):
        """Library may emit lowercase hex; storage must normalise."""
        coord = _make_coord_with_cf_storage(
            {
                "3880CB": _stub_cf_broadcast(
                    "3880cb",
                    "roller_pair",
                    [_stub_cf_member("8b9c", 5, "M02")],
                )
            }
        )

        _run(coord._ingest_cf_broadcasts())

        stored = coord.cf_storage.data["nikobus_cf"]
        self.assertIn("3880CB", stored)
        self.assertEqual(stored["3880CB"]["outputs"][0]["module_address"], "8B9C")

    def test_no_broadcasts_preserves_existing_storage(self):
        """When the library classifies nothing this scan (empty
        broadcast dict), don't wipe the persisted store — preserve
        whatever last scan put there."""
        existing = {"3880CA": {"bus_address": "3880CA", "pattern": "roller_pair", "outputs": []}}
        coord = _make_coord_with_cf_storage({})
        coord.cf_storage.data = {"nikobus_cf": dict(existing)}

        _run(coord._ingest_cf_broadcasts())

        # Untouched.
        self.assertEqual(coord.cf_storage.data["nikobus_cf"], existing)
        coord.cf_storage.async_save.assert_not_called()

    def test_no_discovery_object_is_safe(self):
        coord = MagicMock(spec=NikobusDataCoordinator)
        coord.nikobus_discovery = None
        coord.cf_storage = MagicMock()
        coord.cf_storage.data = {"nikobus_cf": {}}
        coord.cf_storage.async_save = AsyncMock()
        coord._ingest_cf_broadcasts = (
            lambda _self=coord: NikobusDataCoordinator._ingest_cf_broadcasts(_self)
        )
        _run(coord._ingest_cf_broadcasts())
        coord.cf_storage.async_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
