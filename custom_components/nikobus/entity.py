"""Shared entity helpers for the Nikobus integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN
from .coordinator import NikobusDataCoordinator


class NikobusEntity(CoordinatorEntity):
    """Base entity for Nikobus devices with common device info."""

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
        """Return device information for Home Assistant."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=self._device_name,
            manufacturer=BRAND,
            model=self._device_model,
        )


def device_entry_diagnostics(device: DeviceEntry) -> dict[str, Any]:
    """Return diagnostics data for a Nikobus device entry."""
    return {
        "id": device.id,
        "name": device.name,
        "model": device.model,
        "manufacturer": device.manufacturer,
        "sw_version": device.sw_version,
        "identifiers": sorted(device.identifiers),
    }
