import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    """Set up Nikobus button entities from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    entities = []

    if coordinator.dict_button_data:
        for button in coordinator.dict_button_data.get("nikobus_button", {}).values():
            impacted_modules_info = [
                {
                    "address": impacted_module["address"],
                    "group": impacted_module["group"],
                }
                for impacted_module in button.get("impacted_module", [])
            ]

            entity = NikobusButtonEntity(
                hass,
                coordinator,
                button.get("description"),
                button.get("address"),
                button.get("operation_time"),
                impacted_modules_info,
            )

            entities.append(entity)

    async_add_entities(entities)


class NikobusButtonEntity(CoordinatorEntity, ButtonEntity):
    """Represents a Nikobus button entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        description,
        address,
        operation_time,
        impacted_modules_info,
    ) -> None:
        """Initialize the button entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._hass = hass
        self._coordinator = coordinator
        self._description = description
        self._address = address
        self._operation_time = int(operation_time) if operation_time else None
        self.impacted_modules_info = impacted_modules_info

        self._attr_name = f"Nikobus Push Button {address}"
        self._attr_unique_id = f"{DOMAIN}_push_button_{address}"

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
        """Return the state attributes."""
        impacted_modules_str = ", ".join(
            f"{module['address']}_{module['group']}"
            for module in self.impacted_modules_info
        )
        return {"impacted_modules": impacted_modules_str}

    async def async_press(self) -> None:
        """Handle button press."""
        event_data = {
            "address": self._address,
            "operation_time": self._operation_time,
        }
        try:
            await self._coordinator.async_event_handler("ha_button_pressed", event_data)
        except Exception as e:
            _LOGGER.error(
                f"Failed to handle button press for address {self._address}: {e}"
            )

        # Call the coordinator's event handler for button press
        await self._coordinator.async_event_handler("ha_button_pressed", event_data)
