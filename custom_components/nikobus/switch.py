"""Nikobus Switch entity."""
import logging

# Importing required modules from Home Assistant
from homeassistant.components.switch import SwitchEntity
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

    # Iterate over switch modules
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
    ]

    # Add created entities to Home Assistant
    async_add_entities(entities)

# NikobusSwitchEntity class
class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Nikobus Switch Entity."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, channel_description, initial_state=False) -> None:
        """Initialize a Nikobus Switch Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._name = channel_description
        self._state = initial_state
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

    @property
    def should_poll(self):
        """Return True if the entity should be polled for updates."""
        return True

    # is_on property
    @property
    def is_on(self):
        """Return the current state of the switch."""
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
        self.async_schedule_update_ha_state(True)

    async def async_added_to_hass(self):
        """Call when the entity is added to hass."""
        async_dispatcher_connect(
            self.hass,
            f"{UPDATE_SIGNAL}_{self._unique_id}",
            self._schedule_immediate_update,
        )

    async def _schedule_immediate_update(self):
        """Schedule an immediate update."""
        self.async_schedule_update_ha_state(True)

    # Update method
    async def async_update(self):
        """Update the state of the light."""
        self._state= bool(self._dataservice.get_switch_state(self._address, self._channel))

    # async_turn_on method
    async def async_turn_on(self):
        """Turn the switch on."""
        self._state = True
        await self._dataservice.turn_on_switch(self._address, self._channel)
        self.async_write_ha_state()

    # async_turn_off method
    async def async_turn_off(self):
        """Turn the switch off."""
        self._state = False
        await self._dataservice.turn_off_switch(self._address, self._channel)
        self.async_write_ha_state()

    # unique_id property
    @property
    def unique_id(self):
        """Return the unique ID of the switch."""
        return self._unique_id
