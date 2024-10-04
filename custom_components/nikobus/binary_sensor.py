import asyncio
import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus binary sensor entities from a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = []

    if dataservice.api.dict_button_data:
        for button in dataservice.api.dict_button_data["nikobus_button"].values():
            impacted_modules_info = [
                {"address": impacted_module["address"], "group": impacted_module["group"]}
                for impacted_module in button["impacted_module"]
            ]

            entity = NikobusButtonBinarySensor(
                hass,
                dataservice,
                button.get("description"),
                button.get("address"),
                impacted_modules_info,
            )

            entities.append(entity)

        # Register global event listener for all sensors
        register_global_listener(hass, entities)

        async_add_entities(entities)

def register_global_listener(hass: HomeAssistant, sensors: list):
    """Register a single global event listener for all Nikobus sensors."""
    async def handle_event(event):
        for sensor in sensors:
            if event.data['address'] == sensor._address:
                await sensor.handle_button_press_event(event)

    hass.bus.async_listen('nikobus_button_pressed', handle_event)

class NikobusButtonBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Represents a Nikobus button binary sensor entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, address, impacted_modules_info) -> None:
        """Initialize the binary sensor entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._hass = hass
        self._dataservice = dataservice
        self._description = description
        self._address = address
        self.impacted_modules_info = impacted_modules_info
        self._state = False

        self._attr_name = f"Nikobus Sensor {address}"
        self._attr_unique_id = f"{DOMAIN}_{address}"
        self._attr_device_class = "push"

    @callback
    async def handle_button_press_event(self, event):
        """Handle the nikobus_button_pressed event."""
        if event.data['address'] == self._address:
            self._state = True
            self.async_write_ha_state()

            # Delay to simulate the button press state
            await asyncio.sleep(0.5)

            self._state = False
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if the button is pressed, else False."""
        return self._state

    @property
    def device_info(self):
        """Return device information about this binary sensor."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button",
        }

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return extra state attributes of the binary sensor."""
        impacted_modules_str = ", ".join(
            f"{module['address']}_{module['group']}" for module in self.impacted_modules_info
        )
        return {"impacted_modules": impacted_modules_str}

    async def async_update(self):
        """Update method for the binary sensor."""
        # No regular polling is needed, this can be left empty
        pass
