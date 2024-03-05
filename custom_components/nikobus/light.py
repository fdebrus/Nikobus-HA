"""Nikobus Dimmer / Light entity."""

from homeassistant.components.light import LightEntity, SUPPORT_BRIGHTNESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:

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

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False, brightness=255) -> None:
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

    @callback
    def _handle_coordinator_update(self) -> None:
        self._state = bool(self._dataservice.get_light_state(self._address, self._channel))
        self._brightness = self._dataservice.get_light_brightness(self._address, self._channel)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._brightness = kwargs.get("brightness", 255)
        self._state = True
        await self._dataservice.turn_on_light(self._address, self._channel, self._brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._state = False
        await self._dataservice.turn_off_light(self._address, self._channel)
        self.async_write_ha_state()
