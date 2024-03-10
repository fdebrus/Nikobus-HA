"""Nikobus Dimmer / Light entity."""

import logging

from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities) -> bool:

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

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=None, brightness=None) -> None:
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
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def brightness(self):
        return self._brightness

    @property
    def color_mode(self):
        return "brightness"

    @property
    def supported_color_modes(self):
        return {"brightness"}

    @property
    def is_on(self):
        return self._state

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.coordinator.async_add_listener(self._handle_coordinator_update)
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._state = bool(self._dataservice.api.get_light_state(self._address, self._channel))
        self._brightness = self._dataservice.api.get_light_brightness(self._address, self._channel)
        _LOGGER.debug(f"STATE {self._state} - {self._brightness}")
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._brightness = kwargs.get("brightness", 255)
        self._state = True
        await self._dataservice.api.turn_on_light(self._address, self._channel, self._brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._state = False
        await self._dataservice.api.turn_off_light(self._address, self._channel)
        self.async_write_ha_state()
