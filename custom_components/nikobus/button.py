"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Nikobus button entities."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    buttons = coordinator.dict_button_data.get("nikobus_button", {})
    async_add_entities([
        NikobusButtonEntity(coordinator, addr, data)
        for addr, data in buttons.items()
    ])

class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Representation of a Nikobus UI button."""

    def __init__(self, coordinator: NikobusDataCoordinator, address: str, data: dict[str, Any]) -> None:
        """Initialize."""
        super().__init__(coordinator, address, data.get("description", f"Button {address}"), model="Push Button")
        self._attr_unique_id = f"{DOMAIN}_push_button_{address}" # FIXED UNIQUE ID
        self._operation_time = data.get("operation_time")

    async def async_press(self) -> None:
        """Execute press command."""
        await self.coordinator.async_event_handler("ha_button_pressed", {
            "address": self._address,
            "operation_time": self._operation_time,
        })

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle targeted dispatcher updates."""
        super()._handle_coordinator_update()