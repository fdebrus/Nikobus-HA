import logging
import json
import os

from typing import Optional

from homeassistant.components.light import LightEntity, ATTR_BRIGHTNESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)
    entities = []

    # Open the JSON file and load its contents
    current_file_path = os.path.abspath(__file__)
    current_directory = os.path.dirname(current_file_path)
    config_file_path = os.path.join(current_directory, "nikobus_config.json")
    with open(config_file_path, 'r') as file:
        data = json.load(file)

    # Iterate over light modules
    for switch_module in data["dimmer_modules_addresses"]: 
        description = switch_module.get("description")
        model = switch_module.get("model")
        address = switch_module.get("address")
        channels = switch_module["channels"]

        # Iterate over channels
        for i in range(len(channels)):
            channel_description = switch_module.get(channels[i]["description"])
            entities.append(NikobusLightEntity(hass, dataservice, description, model, address, i, channel_description))

    async_add_entities(entities)

class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Nikobus Light Entity."""

    def __init__(self, hass:  HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False, brightness=100) -> None:
        """Initialize a Nikobus Light Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._name = channel_description
        self._state = initial_state
        self._brightness = brightness
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel + 1
        self._attr_name = f"{self._address}_Output_{channel}"
        self._unique_id = f"{self._address}{self._channel}"


    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def name(self):
        """Return the name of the light."""
        return self._name

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def is_on(self):
        """Return true if the light is on."""
        return self._state

    async def async_update(self):
        """Update the state of the light."""
        self._state = self._dataservice.getState(self._address, self._channel)
        # self._brightness

    async def async_update(self):
        """Update the state of the switch."""
        self._state = self._dataservice.getState(self._address, self._channel)
        
    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        await self._dataservice.turn_on_light(self._address, self._channel)

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self._dataservice.turn_off_light(self._address, self._channel)

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id
