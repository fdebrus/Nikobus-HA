"""Binary sensor platform for the Nikobus integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

# Constants for state reset and events
STATE_RESET_DELAY = 1.0  # Seconds before returning to idle
EVENT_BUTTON_PRESSED = "nikobus_button_pressed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nikobus button sensor entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data

    if not coordinator.dict_button_data:
        return

    buttons = coordinator.dict_button_data.get("nikobus_button", {})

    async_add_entities(
        NikobusButtonBinarySensor(
            coordinator=coordinator,
            address=address,
            description=data.get("description", f"Button {address}"),
        )
        for address, data in buttons.items()
    )


class NikobusButtonBinarySensor(NikobusEntity, BinarySensorEntity):
    """Binary sensor representing a physical Nikobus button press."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        description: str,
    ) -> None:
        """Initialize the button binary sensor."""
        super().__init__(
            coordinator=coordinator,
            address=address,
            name=description,
            model="Physical Button",
        )
        self._address = address
        self._attr_name = description
        self._attr_unique_id = f"{DOMAIN}_button_{address}"
        
        self._attr_is_on = False
        self._reset_timer_cancel: Any | None = None

    @property
    def state(self) -> str:
        """Override to return 'pressed' if on, else 'idle'."""
        return "pressed" if self._attr_is_on else "idle"

    async def async_added_to_hass(self) -> None:
        """Register event listeners when added to Home Assistant."""
        await super().async_added_to_hass()
        
        # Listen directly for button press events for this specific address
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESSED, self._handle_button_event)
        )

    @callback
    def _handle_button_event(self, event: Any) -> None:
        """Handle button press events from the Nikobus bus."""
        if event.data.get("address") != self._address:
            return

        _LOGGER.debug("Button %s pressed", self._address)
        
        self._attr_is_on = True
        self.async_write_ha_state()

        # Cancel any existing timer before starting a new one
        if self._reset_timer_cancel:
            self._reset_timer_cancel()

        # Automatically return to 'idle' after the defined delay
        self._reset_timer_cancel = async_call_later(
            self.hass, STATE_RESET_DELAY, self._reset_state
        )

    @callback
    def _reset_state(self, _: datetime) -> None:
        """Reset the sensor state to 'idle'."""
        self._attr_is_on = False
        self._reset_timer_cancel = None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Ignore coordinator updates as this sensor is event-driven."""
        pass