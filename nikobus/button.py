"""Nikobus Button entity."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = []

    for button in dataservice.api.json_button_data["nikobus_button"]:
        impacted_modules_info = [
            {"address": impacted_module["address"], "group": impacted_module["group"]}
            for impacted_module in button["impacted_module"]
        ]

        entity = NikobusButtonEntity(
            hass,
            dataservice,
            button.get("description"),
            button.get("address"),
            impacted_modules_info,
        )

        entities.append(entity)

    async_add_entities(entities)

class NikobusButtonEntity(CoordinatorEntity, ButtonEntity):
    def __init__(self, hass: HomeAssistant, dataservice, description, address, impacted_modules_info) -> None:
        super().__init__(dataservice)
        self._hass = hass
        self._dataservice = dataservice
        self._description = description
        self._address = address
        self.impacted_modules_info = impacted_modules_info  # Now correctly accepting the list of impacted modules

        self._attr_name = f"Nikobus Push Button {address}"
        self._attr_unique_id = f"{DOMAIN}_{address}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button",
        }

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        impacted_modules_str = ", ".join(
            f"{module['address']}_{module['group']}" for module in self.impacted_modules_info
        )
        return {"impacted_modules": impacted_modules_str}

    async def async_press(self) -> None:
        await self._dataservice.async_event_handler("ha_button_pressed", self._address)
