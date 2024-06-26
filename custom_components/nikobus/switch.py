"""Nikobus Switch entity"""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    switch_modules = dataservice.api.dict_module_data.get('switch_module', {})

    entities = [
        NikobusSwitchEntity(
            hass,
            dataservice,
            switch_module_data.get("description"),
            switch_module_data.get("model"),
            address,
            i,
            channel["description"],
        )
        for address, switch_module_data in switch_modules.items()
        for i, channel in enumerate(switch_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)


class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description) -> None:
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._state = None
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
    def is_on(self):
        return self._state

    @callback
    def _handle_coordinator_update(self) -> None:
        self._state = bool(self._dataservice.api.get_switch_state(self._address, self._channel))
        self.async_write_ha_state()

    async def async_turn_on(self):
        self._state = True
        await self._dataservice.api.turn_on_switch(self._address, self._channel)
        self.async_write_ha_state()

    async def async_turn_off(self):
        self._state = False
        await self._dataservice.api.turn_off_switch(self._address, self._channel)
        self.async_write_ha_state()
