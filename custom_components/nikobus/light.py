"""Light platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import BRAND, DOMAIN
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity
from .router import build_unique_id, get_routing

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"
EVENT_BUTTON_OPERATION = "nikobus_button_operation"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus light entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    entities: list[LightEntity] = []
    registered_addresses: set[str] = set()

    routing = get_routing(hass, entry, coordinator.dict_module_data)
    
    for spec in routing.get("light", []):
        if spec.address not in registered_addresses:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, spec.address)},
                manufacturer=BRAND,
                name=spec.module_desc,
                model=spec.module_model,
                via_device=(DOMAIN, HUB_IDENTIFIER),
            )
            registered_addresses.add(spec.address)

        if spec.kind == "dimmer_light":
            entities.append(
                NikobusDimmerEntity(
                    coordinator, spec.address, spec.channel, 
                    spec.channel_description, spec.module_desc, spec.module_model
                )
            )
        elif spec.kind == "relay_switch":
            entities.append(
                NikobusRelayEntity(
                    coordinator, spec.address, spec.channel, 
                    spec.channel_description, spec.module_desc, spec.module_model
                )
            )
        elif spec.kind == "cover_binary":
            entities.append(
                NikobusCoverLightEntity(
                    coordinator, spec.address, spec.channel, 
                    spec.channel_description, spec.module_desc, spec.module_model
                )
            )

    async_add_entities(entities)


class NikobusBaseLight(NikobusEntity, LightEntity, RestoreEntity):
    """Base class for Nikobus light entities with hybrid update logic."""

    def __init__(
        self, coordinator, address, channel, description, module_name, module_model
    ) -> None:
        """Initialize the light base."""
        super().__init__(coordinator, address, module_name, module_model)
        self._address = address
        self._channel = channel
        self._channel_description = description
        self._module_description = module_name
        self._module_model = module_model
        
        self._attr_name = description
        self._is_on: bool | None = None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = super().extra_state_attributes or {}
        attrs.update({
            "nikobus_address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._module_description,
            "module_model": self._module_model,
        })
        return attrs

    async def async_added_to_hass(self) -> None:
        """Register listeners and restore state."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            self._is_on = last_state.state == "on"

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_OPERATION, self._handle_nikobus_event)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Override base to invalidate cache before writing state."""
        self._is_on = None  # Force fresh read from coordinator
        super()._handle_coordinator_update()

    @callback
    def _handle_nikobus_event(self, event: Any) -> None:
        """Handle physical button operation events (Instant path)."""
        if str(event.data.get("impacted_module_address")) != str(self._address):
            return
        
        self._is_on = None
        self.async_write_ha_state()


class NikobusDimmerEntity(NikobusBaseLight):
    """Nikobus dimmer light entity."""

    def __init__(self, *args) -> None:
        """Initialize dimmer."""
        super().__init__(*args)
        self._attr_unique_id = build_unique_id("light", "dimmer_light", self._address, self._channel)
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self.brightness > 0

    @property
    def brightness(self) -> int:
        """Return current brightness 0..255 from coordinator."""
        return self.coordinator.get_light_brightness(self._address, self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the dimmer."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        await self.coordinator.api.turn_on_light(self._address, self._channel, brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the dimmer."""
        await self.coordinator.api.turn_off_light(self._address, self._channel)
        self.async_write_ha_state()


class NikobusRelayEntity(NikobusBaseLight):
    """Nikobus relay-based on/off light."""

    def __init__(self, *args) -> None:
        """Initialize relay."""
        super().__init__(*args)
        self._attr_unique_id = build_unique_id("light", "relay_switch", self._address, self._channel)
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool:
        """Return true if relay is closed."""
        return self.coordinator.get_switch_state(self._address, self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Close relay."""
        await self.coordinator.api.turn_on_switch(self._address, self._channel)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open relay."""
        await self.coordinator.api.turn_off_switch(self._address, self._channel)
        self.async_write_ha_state()


class NikobusCoverLightEntity(NikobusBaseLight):
    """Cover channel used as a binary light switch."""

    def __init__(self, *args) -> None:
        """Initialize cover-as-light."""
        super().__init__(*args)
        self._attr_unique_id = build_unique_id("light", "cover_binary", self._address, self._channel)
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool:
        """Return true if cover is open (acting as ON)."""
        return self.coordinator.get_cover_state(self._address, self._channel) == 0x01

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn light on via cover open command."""
        await self.coordinator.api.open_cover(self._address, self._channel)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn light off via cover stop/close command."""
        await self.coordinator.api.stop_cover(self._address, self._channel, direction="closing")
        self.async_write_ha_state()