import logging
import json

from homeassistant.components.cover import CoverEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)
    entities = []

    # Iterate over cover modules
    for cover_module in dataservice.json_config_data["roller_modules_addresses"]: 
        description = cover_module.get("description")
        model = cover_module.get("model")
        address = cover_module.get("address")
        channels = cover_module["channels"]
        for i in range(len(channels)):
            chDescription = channels[i]["description"]
            entities.append(NikobusCoverEntity(hass, dataservice, description, model, address, i, chDescription))

    async_add_entities(entities)

class NikobusCoverEntity(CoordinatorEntity, CoverEntity):
    """Nikobus Cover Entity."""

    def __init__(self, hass: HomeAssistant, dataservice, description, model, address, channel, chDescription, initial_state="closed") -> None:
        """Initialize a Nikobus Cover Entity."""
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
        """Return the name of the cover."""
        return self._name

    def is_closed(self):
        """Return true if the switch is on."""
        return self._dataservice.get_switch_status(self._address, self._channel)

    def update(self):
        """Update the state of the cover."""
        self._state = self._dataservice.get_output_state(self._address, self._channel)
        return self._state

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        await self._dataservice.open_cover(self._address, self._channel)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        await self._dataservice.close_cover(self._address, self._channel)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        await self._dataservice.stop_cover(self._address, self._channel)

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id
