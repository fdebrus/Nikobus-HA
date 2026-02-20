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

async def async_setup_entry(
    hass: HomeAssistant, 
    entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus button entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    
    # Retrieve buttons from the coordinator's loaded configuration
    buttons = coordinator.dict_button_data.get("nikobus_button", {})
    
    async_add_entities([
        NikobusButtonEntity(coordinator, addr, data)
        for addr, data in buttons.items()
    ])


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Representation of a Nikobus UI button (Software trigger)."""

    def __init__(
        self, 
        coordinator: NikobusDataCoordinator, 
        address: str, 
        data: dict[str, Any]
    ) -> None:
        """Initialize the button entity."""
        
        # FIXED: Robust name handling to prevent "UndefinedType._singleton" logs
        raw_description = data.get("description")
        if not raw_description or str(raw_description) == "UndefinedType._singleton":
            name = f"Button {address}"
        else:
            name = str(raw_description)

        super().__init__(
            coordinator=coordinator, 
            address=address, 
            name=name, 
            model="Push Button"
        )
        
        # Unique ID for the Home Assistant entity registry
        self._attr_unique_id = f"{DOMAIN}_push_button_{address}"
        self._operation_time = data.get("operation_time")

    async def async_press(self) -> None:
        """Execute the button press command on the Nikobus bus."""
        _LOGGER.debug("UI Button pressed for address: %s", self._address)
        
        await self.coordinator.async_event_handler("ha_button_pressed", {
            "address": self._address,
            "operation_time": self._operation_time,
        })

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Stateless entity: Ignore general coordinator updates to reduce log noise.
        This prevents the "Targeted refresh received" log for buttons during polling.
        """
        pass