import logging
import json
import os

from typing import Optional

from homeassistant.components.light import LightEntity, ATTR_BRIGHTNESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

BRIGHTNESS_SCALE = (0, 100)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    # Open the JSON file and load its contents
    current_file_path = os.path.abspath(__file__)
    current_directory = os.path.dirname(current_file_path)
    config_file_path = os.path.join(current_directory, "nikobus_config.json")
    with open(config_file_path, 'r') as file:
        data = json.load(file)

    dimmer_modules_addresses = data['dimmer_modules_addresses']
    entities = []

    for dimmer_module in dimmer_modules_addresses:
        description = dimmer_module.get("description")
        model = dimmer_module.get("model")
        address = dimmer_module.get("address")
        nbrOutputs = dimmer_module.get("nbrOutputs")
    
        if None not in (description, model, address, nbrOutputs):
            for i in range(1, nbrOutputs + 1):
                entities.append(NikobusLightEntity(hass, dataservice, description, model, address, i))
        else:
            _LOGGER.error("Incomplete data for switch module: %s", dimmer_module)

    async_add_entities(entities)

class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Nikobus Light Entity."""

    def __init__(self, hass:  HomeAssistant, dataservice, description, model, address, channel) -> None:
        """Initialize a Nikobus Light Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._attr_name = f"{self._address}_Output_{channel}"
        self._unique_id = f"{self._address}{self._channel}"
        self._brightness = 100

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

#    @property
#    def supported_features(self):
#        """Return the supported features of the light."""
#        return self._supported_features

    @property
    def brightness(self) -> Optional[int]:
        """Return the current brightness."""
        return self._brightness

    @property
    async def is_on(self):
        """Return true if the device is on."""
        return bool(await self._dataservice.is_on(self._address,self._channel))
        
    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        await self._dataservice.turn_on_light(self._address,self._channel)

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self._dataservice.turn_off_light(self._address,self._channel)

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id
