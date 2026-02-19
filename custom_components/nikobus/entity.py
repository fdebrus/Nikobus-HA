"""
Shared entity helpers for the Nikobus integration.

This module provides the NikobusEntity base class, which implements the 
'Targeted Refresh' logic. This ensures entities update instantly when 
the coordinator processes bus data (Scenes or Status frames) without 
relying on a global polling loop.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


class NikobusEntity(CoordinatorEntity[NikobusDataCoordinator]):
    """
    Base class for all Nikobus entities.

    Platinum Requirement: High Performance & Efficiency.
    This class connects to a module-specific dispatcher signal. Instead of 
    waking up every light in the house for one change, only entities on 
    the specific physical module address (e.g., '4707') are refreshed.
    """

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        name: str,
        model: str,
    ) -> None:
        """
        Initialize the Nikobus base entity.

        Args:
            coordinator: The central Nikobus data coordinator.
            address: The physical module address (e.g., '4707').
            name: The descriptive name for the module/device.
            model: The hardware model (e.g., '0B12').
        """
        super().__init__(coordinator)
        self._address = address
        self._device_name = name
        self._device_model = model

        # Group all entities (channels) under 
        # one physical module device in the HA UI.
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=self._device_name,
            manufacturer=BRAND,
            model=self._device_model,
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return diagnostic attributes helpful for bus monitoring."""
        return {
            "nikobus_module_address": self._address,
            "nikobus_module_model": self._device_model,
        }

    async def async_added_to_hass(self) -> None:
        """
        Register targeted signal listener for this specific module address.

        This establishes the link for Scene updates. 
        When a scene is activated, the coordinator receives status frames 
        and dispatches a signal matching this address. This hook ensures 
        this entity 'wakes up' to check the new data.
        """
        await super().async_added_to_hass()
        
        # Unique signal for this physical hardware module
        signal = f"{DOMAIN}_update_{self._address}"
        
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, 
                signal, 
                self._handle_coordinator_update
            )
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle targeted refresh triggered by the coordinator dispatcher.

        Note: When this is called, the coordinator's state buffer already 
        contains the new bus data. This call forces HA to re-evaluate 
        properties like 'is_on' or 'current_cover_position'.
        """
        _LOGGER.debug(
            "Targeted refresh received for %s (Address: %s)", 
            self.name, 
            self._address
        )
        self.async_write_ha_state()


def device_entry_diagnostics(device: dr.DeviceEntry) -> Dict[str, Any]:
    """
    Return diagnostics data for a Nikobus device entry.
    
    Used by the HA Diagnostics integration to export hardware 
    information for troubleshooting.
    """
    return {
        "id": device.id,
        "name": device.name,
        "model": device.model,
        "manufacturer": device.manufacturer,
        "sw_version": device.sw_version,
        "identifiers": sorted(list(device.identifiers)),
    }
