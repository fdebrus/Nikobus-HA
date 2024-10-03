"""Nikobus Dimmer / Light entity"""

import logging
from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

DEFAULT_BRIGHTNESS = 255

async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    dimmer_modules = dataservice.api.dict_module_data.get('dimmer_module', {})

    entities = [
        NikobusLightEntity(
            hass,
            dataservice,
            dimmer_module_data.get("description"),
            dimmer_module_data.get("model"),
            address,
            i,
            channel["description"],
        )
        for address, dimmer_module_data in dimmer_modules.items() 
        for i, channel in enumerate(dimmer_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)

class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Represents a Nikobus light entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description) -> None:
        """Initialize the light entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._state = None
        self._brightness = None
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"

    @property
    def device_info(self):
        """Return device information about this light."""
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
        """Return the color mode of the light."""
        return "brightness"

    @property
    def supported_color_modes(self):
        """Return the supported color modes."""
        return {"brightness"}

    @property
    def is_on(self):
        """Return True if the light is on."""
        return self._state or False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._state = bool(self._dataservice.api.get_light_state(self._address, self._channel))
        self._brightness = self._dataservice.api.get_light_brightness(self._address, self._channel)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn on the light."""
        self._brightness = kwargs.get("brightness", DEFAULT_BRIGHTNESS)
        self._state = True
        try:
            await self._dataservice.api.turn_on_light(self._address, self._channel, self._brightness)
        except Exception as e:
            _LOGGER.error(f"Failed to turn on light at address {self._address}, channel {self._channel}: {e}")
            self._state = False
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        self._state = False
        self._brightness = 0
        try:
            await self._dataservice.api.turn_off_light(self._address, self._channel)
        except Exception as e:
            _LOGGER.error(f"Failed to turn off light at address {self._address}, channel {self._channel}: {e}")
            self._state = True
        self.async_write_ha_state()
