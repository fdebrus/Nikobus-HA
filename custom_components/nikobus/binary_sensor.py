"""Sensor platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus button sensor entities from a config entry."""
    _LOGGER.debug("Setting up Nikobus button sensor entities.")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[NikobusButtonSensor] = []

    if coordinator.dict_button_data:
        for button_data in coordinator.dict_button_data.get(
            "nikobus_button", {}
        ).values():
            entity = NikobusButtonSensor(
                hass=hass,
                coordinator=coordinator,
                description=button_data.get("description", "Unknown Button"),
                address=button_data.get("address", "unknown"),
            )
            entities.append(entity)

    # Register a single global event listener for all sensors
    register_global_listener(hass, entities)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus button sensor entities.", len(entities))


def register_global_listener(
    hass: HomeAssistant, sensors: list[NikobusButtonSensor]
) -> None:
    """Register a single global event listener for all Nikobus sensors."""

    if not sensors:
        return

    address_map = {sensor._address: sensor for sensor in sensors}

    @callback
    async def handle_event(event: Any) -> None:
        """Process button press events for registered sensors."""

        address = event.data.get("address")
        if not address:
            _LOGGER.warning("Received event without address: %s", event.data)
            return

        if sensor := address_map.get(address):
            await sensor._handle_button_event(event)

    remove = hass.bus.async_listen("nikobus_button_pressed", handle_event)
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["button_sensor_remove"] = remove


class NikobusButtonSensor(NikobusEntity, SensorEntity):
    """Represents a Nikobus button sensor entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        description: str,
        address: str,
    ) -> None:
        """Initialize the button sensor entity with data from the Nikobus system configuration."""
        super().__init__(
            coordinator=coordinator,
            module_address=address,
            name=f"Nikobus Button Sensor {address}",
        )
        self._hass = hass
        self._description = description
        self._address = address

        # Keep original unique_id
        self._attr_name = f"Nikobus Button Sensor {address}"
        self._attr_unique_id = f"{DOMAIN}_button_sensor_{address}"
        self._state: str | None = "idle"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information about this sensor."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Button Sensor",
        }

    @property
    def state(self) -> str | None:
        """Return the state of the sensor."""
        return self._state

    @callback
    async def _handle_button_event(self, event: Any) -> None:
        """Handle Nikobus button press events."""
        if event.data.get("address") == self._address:
            _LOGGER.debug("Button sensor %s detected a press event.", self._address)
            self._state = "Pressed"
            self.async_write_ha_state()

            # Optionally reset the state after a short delay
            self._hass.loop.call_later(1, self._reset_state)

    @callback
    def _reset_state(self) -> None:
        """Reset the sensor state to idle after a short delay."""
        self._state = "idle"
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updates from the coordinator if needed."""
        # Since the state is event-driven, no coordinator updates are required.
        pass
