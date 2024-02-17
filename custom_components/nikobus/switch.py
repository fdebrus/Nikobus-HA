"""Nikobus Switch entity."""
import logging
import json
import os

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN, BRAND

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)
    entities = []

    # Open the JSON file and load its contents
    current_file_path = os.path.abspath(__file__)
    current_directory = os.path.dirname(current_file_path)
    config_file_path = os.path.join(current_directory, "nikobus_config.json")
    with open(config_file_path, 'r') as file:
        data = json.load(file)

    # Iterate over switch modules
    for switch_module in data["switch_modules_addresses"]: 
        description = switch_module.get("description")
        model = switch_module.get("model")
        address = switch_module.get("address")
        channels = switch_module["channels"]

        for i in range(len(channels)):
            chDescription = channels[i]["description"]
            entities.append(NikobusSwitchEntity(hass, dataservice, description, model, address, i, chDescription))

    async_add_entities(entities)

class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Nikobus Switch Entity."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, chDescription, initial_state=False) -> None:
        """Initialize a Nikobus Switch Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._name = chDescription
        self._state = initial_state
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel + 1
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
        """Return the name of the switch."""
        return self._name

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._state

    async def async_update(self):
        """Update the state of the switch."""
        self._state = self._dataservice.getOutputState(self._address, self._channel)

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        await self._dataservice.turn_on_switch(self._address, self._channel)

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self._dataservice.turn_off_switch(self._address, self._channel)

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id