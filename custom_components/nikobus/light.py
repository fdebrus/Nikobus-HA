"""Light platform for the Nikobus integration with module-level devices."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from homeassistant.components.light import (
    LightEntity,
    ATTR_BRIGHTNESS,
    ColorMode,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator
from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """
    Set up Nikobus light (dimmer) entities from a config entry.

    Hierarchy:
      - Hub device: 'Nikobus Bridge' (registered in __init__.py)
      - Child device: each dimmer module (registered here)
      - LightEntity: each dimmer channel
    """
    _LOGGER.debug("Setting up Nikobus light entities (modules).")

    coordinator: NikobusDataCoordinator = hass.data[DOMAIN]["coordinator"]
    dimmer_modules: Dict[str, Any] = coordinator.dict_module_data.get("dimmer_module", {})

    device_registry = dr.async_get(hass)
    entities: List[NikobusLightEntity] = []

    for address, dimmer_module_data in dimmer_modules.items():
        module_desc = dimmer_module_data.get("description", f"Dimmer Module {address}")
        module_model = dimmer_module_data.get("model", "Unknown Dimmer Model")

        # 1) Register each dimmer module as a child device of the hub
        _register_nikobus_dimmer_device(
            device_registry=device_registry,
            entry=entry,
            module_address=address,
            module_name=module_desc,
            module_model=module_model,
        )

        # 2) Create a light entity for each channel in the module
        for channel_index, channel_info in enumerate(
            dimmer_module_data.get("channels", []), start=1
        ):
            if channel_info["description"].startswith("not_in_use"):
                continue

            entity = NikobusLightEntity(
                coordinator=coordinator,
                address=address,
                channel=channel_index,
                channel_description=channel_info["description"],
                module_name=module_desc,
                module_model=module_model,
            )
            entities.append(entity)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus light (dimmer) entities.", len(entities))


def _register_nikobus_dimmer_device(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    module_address: str,
    module_name: str,
    module_model: str,
) -> None:
    """
    Register a single Nikobus dimmer module as a child device in the device registry.

    This links the module to the hub device (via_device).
    """
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, module_address)},  # unique ID for this module
        manufacturer=BRAND,
        name=module_name,
        model=module_model,
        # Link module to the hub device
        via_device=(DOMAIN, HUB_IDENTIFIER),
    )


class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """
    Represents a Nikobus light (dimmer channel) in Home Assistant.

    Each entity belongs to a specific module device.
    """

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_name: str,
        module_model: str,
    ) -> None:
        """Initialize the light entity from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._address = address
        self._channel = channel
        self._channel_description = channel_description
        self._module_name = module_name
        self._module_model = module_model

        # For uniqueness, combine domain, module address, and channel
        self._attr_unique_id =  f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_name = channel_description

        # Supported color modes: brightness
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS

        # Internal state variables
        self._is_on: bool | None = None
        self._brightness: int | None = None

    @property
    def device_info(self) -> dict[str, Any]:
        """
        Return device information referencing the dimmer module.

        This ensures the entity is grouped under the module in the UI.
        """
        return {
            "identifiers": {(DOMAIN, self._address)},
            "manufacturer": BRAND,
            "name": self._module_name,
            "model": self._module_model,
        }

    @property
    def is_on(self) -> bool:
        """Return True if the light is on (non-zero brightness)."""
        if self._is_on is not None:
            return self._is_on
        return self.brightness > 0

    @property
    def brightness(self) -> int:
        """Return the brightness of the light (0..255)."""
        if self._brightness is not None:
            return self._brightness

        # Fallback to real-time state from the coordinator
        try:
            return self.coordinator.get_light_brightness(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to get brightness for Nikobus light (addr=%s, channel=%d): %s",
                self._address, self._channel, err
            )
            return 0

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Reset optimistic states
        self._is_on = None
        self._brightness = None
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally with a specified brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        self._is_on = True
        self._brightness = brightness
        self.async_write_ha_state()

        try:
            await self.coordinator.api.turn_on_light(self._address, self._channel, brightness)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn on Nikobus light (addr=%s, channel=%d): %s",
                self._address, self._channel, err
            )
            # Revert optimistic state
            self._is_on = None
            self._brightness = None
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off (brightness=0)."""
        self._is_on = False
        self._brightness = 0
        self.async_write_ha_state()

        try:
            await self.coordinator.api.turn_off_light(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn off Nikobus light (addr=%s, channel=%d): %s",
                self._address, self._channel, err
            )
            # Revert optimistic state
            self._is_on = None
            self._brightness = None
            self.async_write_ha_state()
