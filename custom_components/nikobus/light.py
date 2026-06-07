"""Light platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, operation_signal
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity
from .router import build_unique_id, get_routing, register_output_module_devices

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def _command_error(err: Exception) -> HomeAssistantError:
    """A bus command failed — surface it as a translated HA error so the
    user sees a clean message instead of the raw library exception."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="communication_error",
        translation_placeholders={"error": str(err)},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus light entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[LightEntity] = []

    routing = get_routing(hass, entry, coordinator.dict_module_data)
    specs = routing.get("light", [])
    register_output_module_devices(hass, entry, specs)

    for spec in specs:
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
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes safely."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "nikobus_address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._module_description,
            "module_model": self._module_model,
            "controlled_by": self.coordinator.get_controlled_by(self._address, self._channel),
        }

    async def async_added_to_hass(self) -> None:
        """Register listeners and restore state."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            self._is_on = last_state.state == "on"

        # Per-address signal: only this module's entities are woken on a
        # press, instead of a global EVENT_BUTTON_OPERATION listener that
        # every output entity runs and filters by address.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                operation_signal(self._address),
                self._handle_button_operation,
            )
        )

    def _invalidate_optimistic(self) -> None:
        """Drop the optimistic state so the real hardware state is read."""
        self._is_on = None

    def _render_state(self) -> Any:
        """Diff on the resolved on/off so an unchanged poll skips the write."""
        return self.is_on

    @callback
    def _handle_button_operation(self) -> None:
        """A press impacted this module — drop optimistic state so the
        next read reflects the new hardware state."""
        self._is_on = None
        self.async_write_ha_state()


class NikobusDimmerEntity(NikobusBaseLight):
    """Nikobus dimmer light entity."""

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize dimmer."""
        super().__init__(coordinator, address, channel, description, module_name, module_model)
        self._attr_unique_id = build_unique_id("light", "dimmer_light", self._address, self._channel)
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS
        
        # Add tracker for optimistic slider updates
        self._optimistic_brightness: int | None = None

    @property
    def is_on(self) -> bool:
        """Return optimistic state if set, else coordinator state."""
        if self._is_on is not None:
            return self._is_on
        return self.brightness > 0

    @property
    def brightness(self) -> int:
        """Return optimistic brightness if set, else 0..255 from coordinator."""
        if self._optimistic_brightness is not None:
            return self._optimistic_brightness
        return self.coordinator.get_light_brightness(self._address, self._channel)

    def _invalidate_optimistic(self) -> None:
        """Also drop the optimistic brightness for this dimmer."""
        super()._invalidate_optimistic()
        self._optimistic_brightness = None

    def _render_state(self) -> Any:
        """Diff on resolved on/off + brightness for the dimmer."""
        return (self.is_on, self.brightness)

    @callback
    def _handle_button_operation(self) -> None:
        """Also drop the optimistic brightness for this dimmer's module."""
        self._optimistic_brightness = None
        super()._handle_button_operation()

    def _previous_brightness(self) -> int:
        """Best estimate of the wall LED's current "we last broadcast" state.

        ``led_on`` / ``led_off`` are toggle-on-press button simulations,
        so the library gates them on a real off ↔ on transition. The
        gate needs the brightness AS THE LED LAST SAW IT, which is:
        optimistic (= what we most recently sent, not yet bus-confirmed)
        if set, otherwise the last bus-confirmed value. Mirrors the
        composition in ``brightness`` but is read synchronously before
        we mutate ``_optimistic_brightness`` for the new command.
        """
        if self._optimistic_brightness is not None:
            return self._optimistic_brightness
        return self.coordinator.get_light_brightness(self._address, self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the dimmer with optimistic UI update and error fallback."""
        target_brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        prev_brightness = self._previous_brightness()
        self._is_on = True
        self._optimistic_brightness = target_brightness
        self.async_write_ha_state()

        try:
            await self.coordinator.api.turn_on_light(
                self._address,
                self._channel,
                target_brightness,
                current_brightness=prev_brightness,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self._optimistic_brightness = None
            self.async_write_ha_state()
            raise _command_error(err) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the dimmer with optimistic UI update and error fallback."""
        prev_brightness = self._previous_brightness()
        self._is_on = False
        self._optimistic_brightness = None
        self.async_write_ha_state()

        try:
            await self.coordinator.api.turn_off_light(
                self._address,
                self._channel,
                current_brightness=prev_brightness,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            # Revert UI state on failure
            self._is_on = None
            self._optimistic_brightness = None
            self.async_write_ha_state()
            raise _command_error(err) from err


class NikobusRelayEntity(NikobusBaseLight):
    """Nikobus relay-based on/off light."""

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize relay."""
        super().__init__(coordinator, address, channel, description, module_name, module_model)
        self._attr_unique_id = build_unique_id("light", "relay_switch", self._address, self._channel)
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool:
        """Return optimistic state if set, else coordinator state."""
        if self._is_on is not None:
            return self._is_on
        return self.coordinator.get_switch_state(self._address, self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Close relay with optimistic UI update and error fallback."""
        self._is_on = True
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.turn_on_switch(self._address, self._channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise _command_error(err) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open relay with optimistic UI update and error fallback."""
        self._is_on = False
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.turn_off_switch(self._address, self._channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise _command_error(err) from err


class NikobusCoverLightEntity(NikobusBaseLight):
    """Cover channel used as a binary light switch."""

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize cover-as-light."""
        super().__init__(coordinator, address, channel, description, module_name, module_model)
        self._attr_unique_id = build_unique_id("light", "cover_binary", self._address, self._channel)
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool:
        """Return optimistic state if set, else coordinator state."""
        if self._is_on is not None:
            return self._is_on
        return self.coordinator.get_cover_state(self._address, self._channel) == 0x01

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn light on via cover open command with optimistic UI update and error fallback."""
        self._is_on = True
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.open_cover(self._address, self._channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise _command_error(err) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn light off via cover stop command with optimistic UI update and error fallback."""
        self._is_on = False
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.stop_cover(self._address, self._channel, direction="closing")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise _command_error(err) from err