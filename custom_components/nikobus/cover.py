import logging
import json
import os

from homeassistant.components.cover import CoverEntity

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

    _LOGGER.debug("cover: START")

    # Iterate over cover modules
    for cover_module in data["roller_modules_addresses"]: 
        description = cover_module.get("description")
        model = cover_module.get("model")
        address = cover_module.get("address")
        channels = cover_module["channels"]

        _LOGGER.debug("cover: %s",description)

        # Iterate over channels
        for i in range(len(channels)):
            chDescription = channels[i]["description"]
            _LOGGER.debug("cover: %s",chDescription)
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

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self._state == "closed"  # Adjust according to your implementation

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        await self._dataservice.open_cover(self._address, self._channel)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        await self._dataservice.close_cover(self._address, self._channel)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        await self._dataservice.stop_cover(self._address, self._channel)

    async def async_update(self):
        """Update the state of the cover."""
        self._state = await self._dataservice.getOutputState(self._address, self._channel)

    @property
    def unique_id(self):
        """The unique id of the sensor."""
        return self._unique_id