"""Nikobus Dimmer / Light entity."""
import logging
from typing import Optional

# Importing required modules from Home Assistant
from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

# Importing constants
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

# Entry setup function
async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    # Getting data service from Home Assistant data using entry ID
    dataservice = hass.data[DOMAIN].get(entry.entry_id)
    entities = []

    # Iterate over dimmer modules
    for dimmer_module in dataservice.api.json_config_data["dimmer_modules_addresses"]: 
        description = dimmer_module.get("description")
        model = dimmer_module.get("model")
        address = dimmer_module.get("address")
        channels = dimmer_module["channels"]
        for i in range(len(channels)):
            channel_description = channels[i]["description"]
            # Create NikobusLightEntity instance for each channel
            entities.append(NikobusLightEntity(hass, dataservice, description, model, address, i, channel_description))

    # Add created entities to Home Assistant
    async_add_entities(entities)

# NikobusLightEntity class
class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Nikobus Light Entity."""

    def __init__(self, hass:  HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False, brightness=255) -> None:
        """Initialize a Nikobus Light Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._name = channel_description
        self._state = initial_state
        self._brightness = brightness
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._unique_id = f"{self._address}{self._channel}"

    # device_info property
    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    # name property
    @property
    def name(self):
        """Return the name of the light."""
        return self._name

    # brightness property
    @property
    def brightness(self):
        """Return the brightness of the light."""
        self._brightness = self._dataservice.get_light_brightness(self._address, self._channel)
        return self._brightness

    # supported_features property
    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_BRIGHTNESS 

    # is_on property
    @property
    def is_on(self):
        """Return the current state of the light."""
        self._state = self._dataservice.get_light_state(self._address, self._channel)
        return self._state

    # update method
    def update(self):
        """Update the state of the light."""
        self._state = self._dataservice.get_output_state(self._address, self._channel)
        return self._state

    # async_turn_on method
    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        self._brightness = kwargs.get("brightness", 255)
        await self._dataservice.turn_on_light(self._address, self._channel, self._brightness)
        self._state = True
        self.schedule_update_ha_state()

    # async_turn_off method
    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        await self._dataservice.turn_off_light(self._address, self._channel)
        self._state = False
        self.schedule_update_ha_state()

    # unique_id property
    @property
    def unique_id(self):
        """Return the unique ID of the light."""
        return self._unique_id
