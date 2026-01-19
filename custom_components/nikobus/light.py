"""Light platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import LightEntity, ATTR_BRIGHTNESS, ColorMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity
from .router import build_unique_id, get_routing
from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus light entities (modules)."""
    _LOGGER.debug("Setting up Nikobus light entities (modules).")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    entities: list[LightEntity] = []
    registered_addresses: set[str] = set()

    routing = get_routing(hass, entry, coordinator.dict_module_data)
    for spec in routing["light"]:
        if spec.address not in registered_addresses:
            _register_nikobus_module_device(
                device_registry=device_registry,
                entry=entry,
                module_address=spec.address,
                module_name=spec.module_desc,
                module_model=spec.module_model,
            )
            registered_addresses.add(spec.address)

        if spec.kind == "dimmer_light":
            entities.append(
                NikobusLightEntity(
                    coordinator=coordinator,
                    address=spec.address,
                    channel=spec.channel,
                    channel_description=spec.channel_description,
                    module_name=spec.module_desc,
                    module_model=spec.module_model,
                )
            )
        elif spec.kind == "relay_switch":
            entities.append(
                NikobusRelayLightEntity(
                    coordinator=coordinator,
                    address=spec.address,
                    channel=spec.channel,
                    channel_description=spec.channel_description,
                    module_name=spec.module_desc,
                    module_model=spec.module_model,
                )
            )
        elif spec.kind == "cover_binary":
            entities.append(
                NikobusCoverLightEntity(
                    coordinator=coordinator,
                    address=spec.address,
                    channel=spec.channel,
                    channel_description=spec.channel_description,
                    module_name=spec.module_desc,
                    module_model=spec.module_model,
                )
            )
        else:
            _LOGGER.warning(
                "Unhandled light routing kind '%s' for module %s channel %s.",
                spec.kind,
                spec.address,
                spec.channel,
            )

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus light entities.", len(entities))


def _register_nikobus_module_device(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    module_address: str,
    module_name: str,
    module_model: str,
) -> None:
    """Register a Nikobus module in the device registry."""
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, module_address)},
        manufacturer=BRAND,
        name=module_name,
        model=module_model,
        via_device=(DOMAIN, HUB_IDENTIFIER),
    )


class NikobusLightEntity(NikobusEntity, LightEntity):
    """Represents a Nikobus dimmer light entity within Home Assistant."""

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
        super().__init__(
            coordinator=coordinator,
            address=address,
            name=module_name,
            model=module_model,
        )
        self._address = address
        self._channel = channel
        self._channel_description = channel_description

        self._attr_unique_id = build_unique_id(
            "light", "dimmer_light", self._address, self._channel
        )
        self._attr_name = channel_description

        # Supported color modes: brightness
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS

        # Internal state variables
        self._is_on: bool | None = None
        self._brightness: int | None = None

    @property
    def is_on(self) -> bool:
        """Return True if the light is on (non-zero brightness)."""
        return self._is_on if self._is_on is not None else self.brightness > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._device_name,
            "module_model": self._device_model,
        }

    @property
    def brightness(self) -> int:
        """Return the brightness of the light (0..255)."""
        if self._brightness is not None:
            return self._brightness
        try:
            return self.coordinator.get_light_brightness(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to get brightness for Nikobus light (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            return 0

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
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
            await self.coordinator.api.turn_on_light(
                self._address, self._channel, brightness
            )
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn on Nikobus light (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
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
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self._brightness = None
            self.async_write_ha_state()


class NikobusRelayLightEntity(NikobusEntity, LightEntity):
    """On/off light entity that drives a relay switch channel."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_name: str,
        module_model: str,
    ) -> None:
        super().__init__(
            coordinator=coordinator,
            address=address,
            name=module_name,
            model=module_model,
        )
        self._address = address
        self._channel = channel
        self._channel_description = channel_description

        self._attr_unique_id = build_unique_id(
            "light", "relay_switch", self._address, self._channel
        )
        self._attr_name = channel_description
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

        self._is_on: bool | None = None

    @property
    def is_on(self) -> bool:
        return self._is_on if self._is_on is not None else self._read_current_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._device_name,
            "module_model": self._device_model,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self._is_on = None
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self.async_write_ha_state()
        try:
            await self.coordinator.api.turn_on_switch(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn on Nikobus light relay (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self.async_write_ha_state()
        try:
            await self.coordinator.api.turn_off_switch(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn off Nikobus light relay (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self.async_write_ha_state()

    def _read_current_state(self) -> bool:
        try:
            return self.coordinator.get_switch_state(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to get relay state for Nikobus light (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            return False


class NikobusCoverLightEntity(NikobusEntity, LightEntity):
    """On/off light entity that drives a cover channel as binary output."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_name: str,
        module_model: str,
    ) -> None:
        super().__init__(
            coordinator=coordinator,
            address=address,
            name=module_name,
            model=module_model,
        )
        self._address = address
        self._channel = channel
        self._channel_description = channel_description

        self._attr_unique_id = build_unique_id(
            "light", "cover_binary", self._address, self._channel
        )
        self._attr_name = channel_description
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

        self._is_on: bool | None = None

    @property
    def is_on(self) -> bool:
        return self._is_on if self._is_on is not None else self._read_current_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._device_name,
            "module_model": self._device_model,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self._is_on = None
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self.async_write_ha_state()
        try:
            await self.coordinator.api.open_cover(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to open cover for Nikobus light (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self.async_write_ha_state()
        try:
            await self.coordinator.api.stop_cover(
                self._address, self._channel, direction="closing"
            )
        except NikobusError as err:
            _LOGGER.error(
                "Failed to stop cover for Nikobus light (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self.async_write_ha_state()

    def _read_current_state(self) -> bool:
        try:
            return self.coordinator.get_cover_state(self._address, self._channel) == 0x01
        except NikobusError as err:
            _LOGGER.error(
                "Failed to get cover state for Nikobus light (addr=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            return False
