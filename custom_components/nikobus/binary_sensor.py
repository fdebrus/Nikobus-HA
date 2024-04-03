"""Nikobus Binary_Sensor entity."""

import asyncio
import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = []

    for button in dataservice.api.json_button_data["nikobus_button"].values():
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

    async_add_entities(entities)

class NikobusButtonBinarySensor(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, hass: HomeAssistant, dataservice, description, address, impacted_modules_info) -> None:
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

        self._hass.bus.async_listen('nikobus_button_pressed', self.handle_button_press_event)

    @callback
    async def handle_button_press_event(self, event):
        """Handle the nikobus_button_pressed event."""
        if event.data['address'] == self._address:
            self._state = True
            self.async_write_ha_state()

            await asyncio.sleep(0.5)
            
            self._state = False
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if the button is pressed, else False."""
        return self._state

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button",
        }

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        impacted_modules_str = ", ".join(
            f"{module['address']}_{module['group']}" for module in self.impacted_modules_info
        )
        return {"impacted_modules": impacted_modules_str}

    async def async_update(self):
        """Update method for the binary sensor."""
        pass
