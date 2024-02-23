"""Nikobus Switch entity."""
import logging

# Importing required modules from Home Assistant
from homeassistant.components.switch import SwitchEntity
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

    # Iterate over switch modules
    for switch_module in dataservice.api.json_config_data["switch_modules_addresses"]: 
        description = switch_module.get("description")
        model = switch_module.get("model")
        address = switch_module.get("address")
        channels = switch_module["channels"]
        for i in range(len(channels)):
            channel_description = channels[i]["description"]
            # Create NikobusSwitchEntity instance for each channel
            entities.append(NikobusSwitchEntity(hass, dataservice, description, model, address, i, channel_description))

    # Add created entities to Home Assistant
    async_add_entities(entities)

# NikobusSwitchEntity class
class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Nikobus Switch Entity."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False) -> None:
        """Initialize a Nikobus Switch Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._state = initial_state
        self._name = channel_description
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel
        self._unique_id = f"{self._address}{self._channel}"

    # Device info property
    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    # Name property
    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    # is_on property
    @property
    def is_on(self):
        """Return the current state of the switch."""
        self._state = self._dataservice.get_switch_state(self._address, self._channel)
        return self._state

    # Update method
    def update(self):
        """Update the state of the switch."""
        self._state = self._dataservice.get_output_state(self._address, self._channel)
        return self._state

    # async_turn_on method
    async def async_turn_on(self):
        """Turn the switch on."""
        await self._dataservice.turn_on_switch(self._address, self._channel)
        self._state = True
        self.schedule_update_ha_state()

    # async_turn_off method
    async def async_turn_off(self):
        """Turn the switch off."""
        await self._dataservice.turn_off_switch(self._address, self._channel)
        self._state = False
        self.schedule_update_ha_state()

    # unique_id property
    @property
    def unique_id(self):
        """Return the unique ID of the switch."""
        return self._unique_id
