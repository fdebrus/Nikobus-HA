"""Device-parent resolution (``via_device_id``)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.nikobus.nkbdevices import parent_device_id


class TestParentDeviceId(unittest.TestCase):
    def test_resolves_registered_parent(self):
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = SimpleNamespace(id="dev42")
        self.assertEqual(parent_device_id(registry, "entry1", ("nikobus", "hub")), "dev42")
        registry.async_get_device_by_identifier.assert_called_once_with(("nikobus", "hub"), "entry1")

    def test_missing_parent_or_registry_gives_none(self):
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = None
        self.assertIsNone(parent_device_id(registry, "entry1", ("nikobus", "x")))
        self.assertIsNone(parent_device_id(None, "entry1", ("nikobus", "x")))
        self.assertIsNone(parent_device_id(object(), "entry1", ("nikobus", "x")))


if __name__ == "__main__":
    unittest.main()
