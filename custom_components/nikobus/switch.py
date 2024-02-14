"""Nikobus Switch entity."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND, SWITCH_MODULES_ADDRESSES

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    # for module in SWITCH_MODULES_ADDRESSES:
    entities = [
        NikobusSwitchEntity(hass, dataservice, "A5C9", "1"),
        NikobusSwitchEntity(hass, dataservice, "A5C9", "2"),
        NikobusSwitchEntity(hass, dataservice, "A5C9", "3"),
        NikobusSwitchEntity(hass, dataservice, "A5C9", "4"),
        NikobusSwitchEntity(hass, dataservice, "055B", "1"),
        NikobusSwitchEntity(hass, dataservice, "055B", "2"),
        NikobusSwitchEntity(hass, dataservice, "055B", "3"),
        NikobusSwitchEntity(hass, dataservice, "055B", "4"),
    ]

    async_add_entities(entities)

class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Nikobus Switch Entity."""

    def __init__(self, hass: HomeAssistant, dataservice, module, channel) -> None:
        """Initialize a Aquarite Switch Entity."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._module = module
        self._channel = channel
        self._attr_name = f"{self._module}_Output_{channel}"
        self._unique_id = f"{self._module}{self._channel}"

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._module)},
            "name": "SwitchModule_" + self._module,
            "manufacturer": BRAND,
            "model": "SwitchModule",
        }

    @property
    def is_on(self):
        """Return true if the device is on."""
        return bool(self._dataservice.get_value(self._module,self._channel))
        
    async def async_turn_on(self):
        """Turn the entity on."""
        await self._dataservice.turn_on_switch(self._module,self._channel)

    async def async_turn_off(self):
        """Turn the entity off."""
        await self._dataservice.turn_off_switch(self._module,self._channel)

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id
