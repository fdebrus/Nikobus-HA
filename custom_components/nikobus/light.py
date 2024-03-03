"""Nikobus Dimmer / Light entity."""
import logging
from typing import Optional

# Importing required modules from Home Assistant
from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

# Importing constants
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

UPDATE_SIGNAL = "update_signal"

# Entry setup function
async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    # Getting data service from Home Assistant data using entry ID
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    # Iteration over dimmer modules and their channels
    entities = [
        NikobusLightEntity(
            hass,
            dataservice,
            dimmer_module.get("description"),
            dimmer_module.get("model"),
            dimmer_module.get("address"),
            i,
            channel["description"],
        )
        for dimmer_module in dataservice.api.json_config_data["dimmer_modules_addresses"]
        for i, channel in enumerate(dimmer_module["channels"], start=1)
        if not channel["description"].startswith("not_in_use")
    ]

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
    
    @property
    def should_poll(self):
        """Return True if the entity should be polled for updates."""
        return True

    # brightness property
    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def color_mode(self):
        """Return the color mode of the light."""
        return "brightness"

    @property
    def supported_color_modes(self):
        """Return the supported color modes."""
        return {"brightness"}

    @property
    def is_on(self):
        """Return the current state of the light."""
        return self._state

    async def async_added_to_hass(self):
        """Call when the entity is added to hass."""
        _LOGGER.debug(f"AAA request {UPDATE_SIGNAL}_{self._unique_id}")
        async_dispatcher_connect(
            self.hass,
            f"{UPDATE_SIGNAL}_{self._unique_id}",
            self._schedule_immediate_update,
        )

    async def _schedule_immediate_update(self):
        """Schedule an immediate update."""
        _LOGGER.debug(f"AAA {UPDATE_SIGNAL}_{self._unique_id} REFRESH")
        self.async_schedule_update_ha_state(True)

    # update method
    async def async_update(self):
        """Update the state of the light."""
        self._state = bool(self._dataservice.get_light_state(self._address, self._channel))
        self._brightness = self._dataservice.get_light_brightness(self._address, self._channel)

    # async_turn_on method
    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        self._brightness = kwargs.get("brightness", 255)
        self._state = True
        await self._dataservice.turn_on_light(self._address, self._channel, self._brightness)
        self.async_write_ha_state()

    # async_turn_off method
    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        self._state = False
        await self._dataservice.turn_off_light(self._address, self._channel)
        self.async_write_ha_state()

    # unique_id property
    @property
    def unique_id(self):
        """Return the unique ID of the light."""
        return self._unique_id
