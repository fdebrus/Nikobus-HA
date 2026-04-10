"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BRAND, DOMAIN, HUB_IDENTIFIER
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus button entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data

    entities: list[ButtonEntity] = [
        NikobusPcLinkInventoryButton(coordinator),
        NikobusModuleScanButton(coordinator),
    ]

    if coordinator.dict_button_data:
        buttons = coordinator.dict_button_data.get("nikobus_button", {})
        entities.extend(
            NikobusButtonEntity(coordinator, addr, data)
            for addr, data in buttons.items()
        )

    async_add_entities(entities)


def _hub_device_info() -> dr.DeviceInfo:
    return dr.DeviceInfo(
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        name="Nikobus Bridge",
        manufacturer=BRAND,
        model="PC-Link Bridge",
    )


class NikobusPcLinkInventoryButton(ButtonEntity):
    """Bridge button that starts a PC Link inventory discovery."""

    _attr_has_entity_name = True
    _attr_name = "Discover modules & buttons"
    _attr_icon = "mdi:magnify-scan"
    _attr_should_poll = False

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_pc_link_inventory_button"
        self._attr_device_info = _hub_device_info()

    async def async_press(self) -> None:
        """Start PC Link inventory discovery."""
        _LOGGER.info("PC Link inventory discovery triggered via UI button")
        await self._coordinator.start_pc_link_inventory()


class NikobusModuleScanButton(ButtonEntity):
    """Bridge button that starts a full module scan for button links."""

    _attr_has_entity_name = True
    _attr_name = "Scan all module links"
    _attr_icon = "mdi:cog-sync"
    _attr_should_poll = False

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_module_scan_button"
        self._attr_device_info = _hub_device_info()

    async def async_press(self) -> None:
        """Scan all output modules for button links."""
        _LOGGER.info("Module scan discovery triggered via UI button")
        await self._coordinator.start_module_scan()


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Representation of a Nikobus UI button (Software trigger)."""

    def __init__(
        self, 
        coordinator: NikobusDataCoordinator, 
        address: str, 
        data: dict[str, Any]
    ) -> None:
        """Initialize the button entity."""
        
        raw_desc = str(data.get("description", ""))
        name = raw_desc if raw_desc and "UndefinedType" not in raw_desc else f"Button {address}"

        super().__init__(
            coordinator=coordinator, 
            address=address, 
            name=name, 
            model="Push Button"
        )
        
        # Unique ID for the Home Assistant entity registry
        self._attr_unique_id = f"{DOMAIN}_push_button_{address}"
        self._operation_time = data.get("operation_time")

    async def async_press(self) -> None:
        """Execute the button press command on the Nikobus bus."""
        _LOGGER.debug("UI Button pressed for address: %s", self._address)
        
        await self.coordinator.async_event_handler("ha_button_pressed", {
            "address": self._address,
            "operation_time": self._operation_time,
        })

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Stateless entity: Ignore general coordinator updates to reduce log noise.
        This prevents the "Targeted refresh received" log for buttons during polling.
        """
        pass