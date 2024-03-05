"""Nikobus Dimmer / Light entity."""

import logging
from typing import Optional

from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

UPDATE_SIGNAL = "update_signal"

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus light entities from a configuration entry.
    
    This function initializes light entities based on the dimmer modules configured in the Nikobus system.
    """
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

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

    async_add_entities(entities)

class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Represents a Nikobus dimmer (light) entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False, brightness=255) -> None:
        """Initialize the Nikobus Light Entity with specific parameters."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._state = initial_state
        self._brightness = brightness
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"

    @property
    def device_info(self):
        """Return device information for Home Assistant."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def color_mode(self):
        """Return the color mode of the light, supporting only brightness."""
        return "brightness"

    @property
    def supported_color_modes(self):
        """Return the supported color modes, which is brightness only for this entity."""
        return {"brightness"}

    @property
    def is_on(self):
        """Return the current state of the light (on/off)."""
        return self._state

    @callback
    def _handle_coordinator_update(self) -> None:
        self._state = bool(self._dataservice.get_light_state(self._address, self._channel))
        self._brightness = self._dataservice.get_light_brightness(self._address, self._channel)
        _LOGGER.debug(f"LIGHT COORDINATOR UPDATE {self._state} - {self._brightness}.")
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the light on with the specified brightness."""
        self._brightness = kwargs.get("brightness", 255)
        self._state = True
        await self._dataservice.turn_on_light(self._address, self._channel, self._brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        self._state = False
        await self._dataservice.turn_off_light(self._address, self._channel)
        self.async_write_ha_state()
