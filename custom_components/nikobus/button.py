"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BRAND, CONF_PRIOR_GEN3
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

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

            discovery_info = button_data.get("discovered_info", [])
            discovery_info = discovery_info[0] if discovery_info else {}

            entity = NikobusButtonEntity(
                hass=hass,
                coordinator=coordinator,
                config_entry=entry,
                description=button_data.get("description", "Unknown Button"),
                address=button_data.get("address", "unknown"),
                operation_time=button_data.get("operation_time"),
                impacted_modules_info=impacted_modules_info,
                discovery_type=discovery_info.get("type"),
                discovery_model=discovery_info.get("model"),
                discovery_address=discovery_info.get("address"),
                discovery_channel=discovery_info.get("channels"),
                discovery_key=discovery_info.get("key"),
            )
            entities.append(entity)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus button entities.", len(entities))


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Represents a Nikobus button entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        config_entry: ConfigEntry,
        description: str,
        address: str,
        operation_time: int | None,
        impacted_modules_info: list[dict[str, Any]],
        discovery_type: str,
        discovery_model: str,
        discovery_address: str,
        discovery_channel: str,
        discovery_key: str,
    ) -> None:
        """Initialize the button entity with data from the Nikobus system configuration."""
        super().__init__(
            coordinator=coordinator,
            module_address=address,
            name=f"Nikobus Push Button {address}",
        )
        self._hass = hass
        self._description = description
        self._address = address
        self._operation_time = int(operation_time) if operation_time else None
        self.impacted_modules_info = impacted_modules_info
        self.discovery_type = discovery_type
        self.discovery_model = discovery_model
        self.discovery_address = discovery_address
        self.discovery_channel = discovery_channel
        self.discovery_key = discovery_key

        # Original unique_id
        self._attr_name = f"Nikobus Push Button {address}"
        self._attr_unique_id = f"{DOMAIN}_push_button_{address}"

        # Option set in the config entry
        self._prior_gen3: bool = config_entry.data.get(CONF_PRIOR_GEN3, False)

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information about this button."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self.discovery_model or "Push Button",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        attributes: dict[str, Any] = {
            "type": self.discovery_type,
            "model": self.discovery_model,
            "address": self.discovery_address,
            "channel": self.discovery_channel,
            "key": self.discovery_key,
        }

        if self.impacted_modules_info:
            attributes["user_impacted_modules"] = ", ".join(
                f"{module['address']}_{module['group']}" for module in self.impacted_modules_info
            )

        return attributes

    async def async_press(self) -> None:
        """Handle button press event."""
        event_data = {
            "address": self._address,
            "operation_time": self._operation_time,
        }
        try:
            _LOGGER.info("Processing HA button press: %s", self._address)
            await self.coordinator.async_event_handler("ha_button_pressed", event_data)

            if not self._prior_gen3:
                await self._refresh_impacted_modules()
        except Exception as err:
            _LOGGER.error(
                "Failed to handle button press for %s: %s",
                self._address,
                err,
                exc_info=True,
            )

    async def _refresh_impacted_modules(self) -> None:
        """Refresh states for modules affected by the button press."""
        if not self.impacted_modules_info:
            return

        for module in self.impacted_modules_info:
            module_address, module_group = module["address"], module["group"]
            try:
                _LOGGER.debug(
                    "Refreshing module %s, group %s", module_address, module_group
                )
                value = await self.coordinator.nikobus_command.get_output_state(
                    module_address, module_group
                )
                if value is None:
                    _LOGGER.warning(
                        "No output state returned for module %s, group %s",
                        module_address,
                        module_group,
                    )
                    continue

                self.coordinator.set_bytearray_group_state(
                    module_address, module_group, value
                )
                _LOGGER.debug(
                    "Updated state for module %s, group %s",
                    module_address,
                    module_group,
                )
            except Exception as inner_err:
                _LOGGER.error(
                    "Failed to refresh module %s, group %s: %s",
                    module_address,
                    module_group,
                    inner_err,
                    exc_info=True,
                )
