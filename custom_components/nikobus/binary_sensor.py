import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus button sensor entities from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    entities = []

    if coordinator.dict_button_data:
        for button in coordinator.dict_button_data.get("nikobus_button", {}).values():
            entity = NikobusButtonSensor(
                hass,
                coordinator,
                button.get("description"),
                button.get("address"),
            )
            entities.append(entity)

    async_add_entities(entities)


class NikobusButtonSensor(CoordinatorEntity, SensorEntity):
    """Represents a Nikobus button sensor entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        description: str,
        address: str,
    ) -> None:
        """Initialize the button sensor entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._hass = hass
        self._coordinator = coordinator
        self._description = description
        self._address = address

        self._attr_name = f"Nikobus Button Sensor {address}"
        self._attr_unique_id = f"{DOMAIN}_button_sensor_{address}"
        self._state = None

        # Register for button press events
        self._hass.bus.async_listen("nikobus_button_pressed", self._handle_button_event)

    @property
    def device_info(self):
        """Return device information about this sensor."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Button Sensor",
        }

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @callback
    def _handle_button_event(self, event):
        """Handle Nikobus button press events."""
        event_data = event.data
        address = event_data.get("address")

        if address == self._address:
            _LOGGER.debug(f"Button sensor {self._address} detected a press event.")
            self._state = "Pressed"
            self.async_write_ha_state()

            # Optionally reset the state after a short delay
            self._hass.loop.call_later(1, self._reset_state)

    @callback
    def _reset_state(self):
        """Reset the sensor state to None."""
        self._state = None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updates from the coordinator."""
        # Update the state if necessary
        pass  # Since the state is event-driven, we may not need to handle coordinator updates
