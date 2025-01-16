"""***FINAL*** Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus button entities from a config entry."""
    _LOGGER.debug("Setting up Nikobus button entities.")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[NikobusButtonEntity] = []

    if coordinator.dict_button_data:
        for button_data in coordinator.dict_button_data.get(
            "nikobus_button", {}
        ).values():
            impacted_modules_info = [
                {"address": module["address"], "group": module["group"]}
                for module in button_data.get("impacted_module", [])
            ]

            entity = NikobusButtonEntity(
                hass=hass,
                coordinator=coordinator,
                description=button_data.get("description", "Unknown Button"),
                address=button_data.get("address", "unknown"),
                operation_time=button_data.get("operation_time"),
                impacted_modules_info=impacted_modules_info,
            )
            entities.append(entity)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus button entities.", len(entities))


class NikobusButtonEntity(CoordinatorEntity, ButtonEntity):
    """Represents a Nikobus button entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        description: str,
        address: str,
        operation_time: int | None,
        impacted_modules_info: list[dict[str, Any]],
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
    def device_info(self) -> dict[str, Any]:
        """Return device information about this button."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Push Button",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.impacted_modules_info:
            return None

        return {
            "impacted_modules": ", ".join(
                f"{module['address']}_{module['group']}"
                for module in self.impacted_modules_info
            )
        }

    async def async_press(self) -> None:
        """Handle button press event."""
        event_data = {
            "address": self._address,
            "operation_time": self._operation_time,
        }
        try:
            _LOGGER.info("Processing Nikobus button press: %s", self._address)
            await self._coordinator.async_event_handler("ha_button_pressed", event_data)

            # Refresh state of impacted modules
            for module in self.impacted_modules_info:
                module_address, module_group = module["address"], module["group"]
                _LOGGER.debug(
                    "Refreshing module %s, group %s", module_address, module_group
                )

                value = (
                    await self._coordinator.nikobus_command_handler.get_output_state(
                        module_address, module_group
                    )
                )

                if value is not None:
                    self._coordinator.set_bytearray_group_state(
                        module_address, module_group, value
                    )

        except Exception as err:
            _LOGGER.error(
                "Failed to handle button press for %s: %s", self._address, err
            )
