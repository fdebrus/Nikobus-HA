"""Shared entity helpers for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


class NikobusEntity(CoordinatorEntity[NikobusDataCoordinator]):
    """Base entity for Nikobus devices with targeted refresh support."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        name: str,
        model: str,
    ) -> None:
        """Initialize the entity with shared device information."""
        super().__init__(coordinator)
        self._address = address
        self._device_name = name
        self._device_model = model

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for Home Assistant grouping."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=self._device_name,
            manufacturer=BRAND,
            model=self._device_model,
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return shared state attributes for Nikobus entities."""
        return {
            "nikobus_module_address": self._address,
            "nikobus_module_model": self._device_model,
        }

    async def async_added_to_hass(self) -> None:
        """Register targeted signal listener for this specific module address."""
        await super().async_added_to_hass()
        
        # This is the "Platinum" hook: listen only for this module's address signal
        signal = f"{DOMAIN}_update_{self._address}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle targeted refresh triggered by the coordinator dispatcher."""
        _LOGGER.debug("Targeted refresh for %s (Address: %s)", self.name, self._address)
        # Force the entity to re-read values from the coordinator's state buffer
        self.async_write_ha_state()


def device_entry_diagnostics(device: DeviceEntry) -> Dict[str, Any]:
    """Return diagnostics data for a Nikobus device entry."""
    return {
        "id": device.id,
        "name": device.name,
        "model": device.model,
        "manufacturer": device.manufacturer,
        "sw_version": device.sw_version,
        "identifiers": sorted(list(device.identifiers)),
    }