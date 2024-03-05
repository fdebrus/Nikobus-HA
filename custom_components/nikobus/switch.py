"""Nikobus Switch entity."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

UPDATE_SIGNAL = "update_signal"

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus switch entities from a configuration entry.
    
    This function initializes switch entities based on the switch modules configured in the Nikobus system.
    """
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = [
        NikobusSwitchEntity(
            hass,
            dataservice,
            switch_module.get("description"),
            switch_module.get("model"),
            switch_module.get("address"),
            i,
            channel["description"],
        )
        for switch_module in dataservice.api.json_config_data["switch_modules_addresses"]
        for i, channel in enumerate(switch_module["channels"], start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)

class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Represents a Nikobus switch entity within Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False) -> None:
        """Initialize the Nikobus Switch Entity with specific parameters."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._name = channel_description
        self._state = initial_state
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._unique_id = f"{self._address}{self._channel}"

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
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def is_on(self):
        """Return the current state of the switch (on/off)."""
        return self._state

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._state = bool(self._dataservice.get_switch_state(self._address, self._channel))
        _LOGGER.debug(f"SWITCH COORDINATOR UPDATE {self._state}.")
        self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the switch on."""
        self._state = True
        await self._dataservice.turn_on_switch(self._address, self._channel)
        self.async_write_ha_state()

    async def async_turn_off(self):
        """Turn the switch off."""
        self._state = False
        await self._dataservice.turn_off_switch(self._address, self._channel)
        self.async_write_ha_state()

    @property
    def unique_id(self):
        """Return the unique ID for this switch entity."""
        return self._unique_id
