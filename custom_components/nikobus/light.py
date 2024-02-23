import logging
import json

from typing import Optional

from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)
    entities = []

    # Iterate over dimmer modules
    for dimmer_module in dataservice.api.json_config_data["dimmer_modules_addresses"]: 
        description = dimmer_module.get("description")
        model = dimmer_module.get("model")
        address = dimmer_module.get("address")
        channels = dimmer_module["channels"]
        for i in range(len(channels)):
            ch_description = channels[i]["description"]
            entities.append(NikobusLightEntity(hass, dataservice, description, model, address, i, ch_description))

    async_add_entities(entities)

class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Nikobus Light Entity."""

    def __init__(self, hass:  HomeAssistant, dataservice, description, model, address, channel, ch_description, initial_state=False, brightness=255) -> None:
        """Initialize a Nikobus Light Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._name = ch_description
        self._state = initial_state
        self._brightness = brightness
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
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
        self._brightness = self._dataservice.get_light_brightness(self._address, self._channel)
        return self._brightness

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_BRIGHTNESS 

    @property
    def is_on(self):
        self._state = self._dataservice.get_light_state(self._address, self._channel)
        return self._state

    def update(self):
        """Update the state of the cover."""
        self._state = self._dataservice.get_output_state(self._address, self._channel)
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        self._brightness = kwargs.get("brightness", 255)
        await self._dataservice.turn_on_light(self._address, self._channel, self._brightness)
        self._state = True
        self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self._dataservice.turn_off_light(self._address, self._channel)
        self._state = False
        self.schedule_update_ha_state()
        
    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id
