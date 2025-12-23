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
_BUTTON_REGISTRY_KEY = "button_sensor_registry"
_BUTTON_LISTENER_KEY = "button_sensor_listener"


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
    register_global_listener(hass)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus button sensor entities.", len(entities))


def _get_button_registry(hass: HomeAssistant) -> dict[str, NikobusButtonSensor]:
    """Return the shared button sensor registry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(_BUTTON_REGISTRY_KEY, {})


def register_global_listener(hass: HomeAssistant) -> None:
    """Register a single global event listener for all Nikobus sensors."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_BUTTON_LISTENER_KEY):
        return

    @callback
    async def handle_event(event: Any) -> None:
        """Process button press events for registered sensors."""
        try:
            address = event.data.get("address")
            if not address:
                _LOGGER.warning("Received event without address: %s", event.data)
                return
            sensor = _get_button_registry(hass).get(address)
            if sensor is None:
                _LOGGER.debug(
                    "No registered button sensor for address %s (event=%s)",
                    address,
                    event.data,
                )
                return
            await sensor.async_handle_button_event(event)
        except Exception as e:
            _LOGGER.error(
                "Error handling nikobus_button_pressed event: %s", e, exc_info=True
            )

    domain_data[_BUTTON_LISTENER_KEY] = hass.bus.async_listen(
        "nikobus_button_pressed", handle_event
    )


class NikobusButtonSensor(NikobusEntity, BinarySensorEntity):
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
            address=address,
            name=description,
            model="Button Sensor",
        )
        self._hass = hass

        self._attr_name = f"Nikobus Button Sensor {address}"
        self._attr_unique_id = f"{DOMAIN}_button_sensor_{address}"
        self._attr_is_on = False
        self._reset_cancel: Any | None = None

    async def async_added_to_hass(self) -> None:
        """Register the sensor in the shared registry."""
        await super().async_added_to_hass()
        _get_button_registry(self.hass)[self._address] = self

    async def async_will_remove_from_hass(self) -> None:
        """Remove the sensor from the shared registry."""
        _get_button_registry(self.hass).pop(self._address, None)
        self._cancel_reset()
        await super().async_will_remove_from_hass()

    @callback
    async def async_handle_button_event(self, event: Any) -> None:
        """Handle Nikobus button press events."""
        if event.data.get("address") == self._address:
            _LOGGER.debug("Button sensor %s detected a press event.", self._address)
            self._attr_is_on = True
            self.async_write_ha_state()

            # Reset the state after a short delay
            self._cancel_reset()
            self._reset_cancel = async_call_later(
                self._hass, 1, self._async_reset_state
            )

    @callback
    def _async_reset_state(self, _: datetime) -> None:
        """Reset the sensor state to idle after a short delay."""
        self._reset_cancel = None
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updates from the coordinator if needed."""
        pass  # Since the state is event-driven, no coordinator updates are required.

    @callback
    def _cancel_reset(self) -> None:
        """Cancel any pending reset callback."""
        if self._reset_cancel:
            self._reset_cancel()
            self._reset_cancel = None
