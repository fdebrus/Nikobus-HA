"""Nikobus Button entity"""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus button entities from a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = []

    if dataservice.api.dict_button_data:
        for button in dataservice.api.dict_button_data.get("nikobus_button", {}).values():
            impacted_modules_info = [
                {"address": impacted_module["address"], "group": impacted_module["group"]}
                for impacted_module in button.get("impacted_module", [])
            ]

            entity = NikobusButtonEntity(
                dataservice,
                button.get("description"),
                button.get("address"),
                impacted_modules_info,
            )

            entities.append(entity)

    async_add_entities(entities)

class NikobusButtonEntity(CoordinatorEntity, ButtonEntity):
    """Represents a Nikobus button entity within Home Assistant."""

    def __init__(self, dataservice, description, address, impacted_modules_info) -> None:
        """Initialize the button entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._description = description
        self._address = address
        self.impacted_modules_info = impacted_modules_info

        self._attr_name = f"Nikobus Push Button {address}"
        self._attr_unique_id = f"{DOMAIN}_{address}"

    @property
    def device_info(self):
        """Return device information about this button."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button",
        }

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return extra state attributes of the button."""
        impacted_modules_str = ", ".join(
            f"{module['address']}_{module['group']}" for module in self.impacted_modules_info
        )
        return {"impacted_modules": impacted_modules_str}

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self._dataservice.async_event_handler("ha_button_pressed", self._address)
        except Exception as e:
            _LOGGER.error(f"Error during button press handling for address {self._address}: {e}")
