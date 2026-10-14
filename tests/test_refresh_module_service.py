"""``nikobus.refresh_module`` and ``homeassistant.update_entity`` read a module group on demand."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ServiceValidationError

from custom_components.nikobus import REFRESH_MODULE_SCHEMA, SERVICE_REFRESH_MODULE
from custom_components.nikobus.entity import NikobusEntity


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


class TestSchema(unittest.TestCase):
    def test_service_name(self):
        self.assertEqual(SERVICE_REFRESH_MODULE, "refresh_module")
        # The test stubs replace voluptuous; validate only with the real one.
        if callable(REFRESH_MODULE_SCHEMA):
            self.assertEqual(dict(REFRESH_MODULE_SCHEMA({"address": "81F6", "group": "2"}))["group"], 2)


class TestHandler(unittest.TestCase):
    def _handler(self, coordinator):
        """Register the services on a stub hass and return the refresh handler."""
        from custom_components.nikobus import async_setup

        hass = MagicMock()
        registered: dict[str, object] = {}
        hass.services.async_register = (
            lambda domain, name, handler, schema=None, **_kw: registered.__setitem__(name, handler)
        )
        with patch("custom_components.nikobus._loaded_coordinator", return_value=coordinator):
            _run(async_setup(hass, {}))
            return registered[SERVICE_REFRESH_MODULE]

    def test_reads_the_module_and_passes_the_group(self):
        coordinator = MagicMock()
        coordinator.async_refresh_module = AsyncMock(return_value=True)
        handler = self._handler(coordinator)
        call = MagicMock(data={"address": " 81f6 ", "group": 2})
        with patch("custom_components.nikobus._loaded_coordinator", return_value=coordinator):
            _run(handler(call))
        coordinator.async_refresh_module.assert_awaited_once_with("81F6", 2)

    def test_unknown_module_is_a_validation_error(self):
        coordinator = MagicMock()
        coordinator.async_refresh_module = AsyncMock(return_value=False)
        handler = self._handler(coordinator)
        with (
            patch("custom_components.nikobus._loaded_coordinator", return_value=coordinator),
            self.assertRaises(ServiceValidationError) as cm,
        ):
            _run(handler(MagicMock(data={"address": "A3EE42"})))
        self.assertEqual(cm.exception.translation_key, "unknown_output_module")

    def test_missing_address_is_a_validation_error(self):
        handler = self._handler(MagicMock())
        with self.assertRaises(ServiceValidationError) as cm:
            _run(handler(MagicMock(data={"address": ""})))
        self.assertEqual(cm.exception.translation_key, "missing_module_address")


class TestEntityUpdate(unittest.TestCase):
    def _entity(self, refreshed: bool, channel=None):
        coordinator = MagicMock()
        coordinator.hass = None
        coordinator.config_entry = None
        coordinator.async_refresh_module = AsyncMock(return_value=refreshed)
        coordinator.async_request_refresh = AsyncMock()
        entity = NikobusEntity(coordinator, "81F6", "S3", "05-002")
        if channel is not None:
            entity._channel = channel
        return entity, coordinator

    def test_an_output_entity_reads_its_own_group(self):
        entity, coordinator = self._entity(True, channel=6)
        _run(entity.async_update())
        coordinator.async_refresh_module.assert_awaited_once_with("81F6", channel=6)
        coordinator.async_request_refresh.assert_not_called()

    def test_anything_else_falls_back_to_the_coordinator_refresh(self):
        entity, coordinator = self._entity(False)
        _run(entity.async_update())
        coordinator.async_refresh_module.assert_awaited_once_with("81F6", channel=None)
        coordinator.async_request_refresh.assert_awaited_once()
