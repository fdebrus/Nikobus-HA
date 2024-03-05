"""Nikobus Button entity."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BRAND

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:

    dataservice = hass.data[DOMAIN].get(entry.entry_id)

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

    async_add_entities(entities)

class NikobusButtonEntity(CoordinatorEntity, ButtonEntity):

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
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button"
        }

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        return {"impacted_module": f"{self.impacted_module_address}_{self.impacted_module_group}"}

    async def async_press(self) -> None:
        await self._dataservice.send_button_press(self._address)
