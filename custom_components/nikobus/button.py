"""Nikobus Button entity."""

import logging

# Importing necessary classes from Home Assistant for button entities
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Importing constants used in the integration
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    # Retrieve the data service associated with the configuration entry
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    # Create a list of NikobusButtonEntity instances from the data provided by the dataservice
    entities = [
        NikobusButtonEntity(
            hass,
            dataservice,
            button.get("description"),
            button.get("address"),
            impacted_module["address"],
            impacted_module["group"],
        )
        for button in dataservice.api.json_button_data["nikobus_button"]
        for i, impacted_module in enumerate(button["impacted_module"])
    ]

    # Add the entities to Home Assistant
    async_add_entities(entities)

class NikobusButtonEntity(CoordinatorEntity, ButtonEntity):
    """Represents a Nikobus Button in Home Assistant."""

    def __init__(self, hass: HomeAssistant, dataservice, description, address, impacted_module_address, impacted_module_group) -> None:
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._description = description
        self._address = address 
        self.impacted_module_address = impacted_module_address
        self.impacted_module_group = impacted_module_group

        self._attr_name = f"Nikobus Push Button {address}"
        self._attr_unique_id = f"{DOMAIN}_{self._address}"

    @property
    def device_info(self):
        """Return device information for this button entity."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button"
        }

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return extra state attributes for this button entity."""
        return {"impacted_module": f"{self.impacted_module_address}_{self.impacted_module_group}"}

    async def async_press(self) -> None:
        """Handle the button press event.

        This method is called when the button is pressed in the Home Assistant UI.
        It triggers the corresponding action in the Nikobus system.
        """
        _LOGGER.debug("Nikobus Button Pressed")
        await self._dataservice.send_button_press(self._address)
