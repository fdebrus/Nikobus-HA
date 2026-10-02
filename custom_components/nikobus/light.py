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
from nikobus_connect.rgb import (
    RGB_OFF_ROLES,
    RGB_ON_ROLES,
    ROLE_OFF,
    ROLE_ON,
    RgbState,
    decode_rgb_state,
)

from .const import DOMAIN, operation_signal
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity, command_error
from .router import (
    build_unique_id,
    choose_rgb_key,
    get_routing,
    register_output_module_devices,
    rgb_light_unique_id,
    rgb_links_for,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


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

    # One light per RGB controller. The router skips opaque modules, so
    # the controller is picked up here from its own bucket; its device is
    # registered by the button platform with the other opaque modules and
    # the entity attaches to it by address.
    rgb_bucket = (coordinator.dict_module_data or {}).get("rgb_module") or {}
    if isinstance(rgb_bucket, dict):
        for address, module_data in sorted(rgb_bucket.items()):
            if not isinstance(module_data, dict):
                continue
            entities.append(
                NikobusRgbLight(
                    coordinator,
                    str(address).upper(),
                    str(
                        module_data.get("nkb_name")
                        or module_data.get("description")
                        or f"RGB controller ({address})"
                    ),
                    str(module_data.get("model") or "340-00112"),
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
                failure_handler=self._command_failed,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self._optimistic_brightness = None
            self.async_write_ha_state()
            raise command_error(err) from err

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
                failure_handler=self._command_failed,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            # Revert UI state on failure
            self._is_on = None
            self._optimistic_brightness = None
            self.async_write_ha_state()
            raise command_error(err) from err


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
            await self.coordinator.api.turn_on_switch(
                self._address, self._channel, failure_handler=self._command_failed
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise command_error(err) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open relay with optimistic UI update and error fallback."""
        self._is_on = False
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.turn_off_switch(
                self._address, self._channel, failure_handler=self._command_failed
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise command_error(err) from err


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
            await self.coordinator.api.open_cover(
                self._address, self._channel, failure_handler=self._command_failed
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise command_error(err) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn light off via cover stop command with optimistic UI update and error fallback."""
        self._is_on = False
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.stop_cover(
                self._address, self._channel, direction="opening", failure_handler=self._command_failed
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise command_error(err) from err

class NikobusRgbLight(NikobusBaseLight):
    """The 340-00112 RGB controller as an on/off light with a colour readout.

    The controller answers the state query with an on flag and one
    lit-or-not flag per colour output — no levels — and accepts no
    set-output command: it is driven only by the keys linked to it. So
    the light shows on/off and the colour the lit outputs mix to, and
    switches by pressing a linked key: the key whose role is ``on`` or
    ``off`` in its link's mode, or the best substitute (a toggle, the
    scenario start/stop, a preset). Which keys are linked is known only
    from the ``.nkb`` project file; without an import, or without a key
    of a usable role, the light is a readout and says so when asked to
    switch. Colour and brightness cannot be set — the bus offers no way.
    """

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        module_name: str,
        module_model: str,
    ) -> None:
        """One light per controller, on the controller's device."""
        super().__init__(coordinator, address, 1, module_name, module_name, module_model)
        # The device carries the name; the single light takes it.
        self._attr_name = None
        self._attr_unique_id = rgb_light_unique_id(address)
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    def _state(self) -> RgbState:
        """The controller's last polled image, decoded."""
        return decode_rgb_state(self.coordinator.get_bytearray_group_state(self._address, 1))

    def _links(self) -> list[dict[str, Any]]:
        return rgb_links_for(self.coordinator.dict_module_data, self._address)

    @property
    def is_on(self) -> bool:
        """Optimistic state if set, else the polled on flag."""
        if self._is_on is not None:
            return self._is_on
        return self._state().on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The colour flags, the colour they mix to, and the keys that drive it."""
        state = self._state()
        links = self._links()
        return {
            **super().extra_state_attributes,
            "colour": state.colour_name,
            "red": state.red,
            "green": state.green,
            "blue": state.blue,
            "linked_keys": [
                {
                    "bus_address": link.get("bus_address"),
                    "wall_button_address": link.get("button_address"),
                    "wall_button_key": link.get("key"),
                    "mode": link.get("mode_label"),
                    "role": link.get("role"),
                }
                for link in links
            ],
            "on_key": (choose_rgb_key(links, RGB_ON_ROLES) or {}).get("bus_address"),
            "off_key": (choose_rgb_key(links, RGB_OFF_ROLES) or {}).get("bus_address"),
        }

    def _render_state(self) -> Any:
        """Diff on on/off and the colour, which is what the card shows."""
        return (self.is_on, self._state().colour_name)

    async def _press(self, turn_on: bool) -> None:
        roles = RGB_ON_ROLES if turn_on else RGB_OFF_ROLES
        link = choose_rgb_key(self._links(), roles)
        if link is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rgb_no_key",
                translation_placeholders={
                    "address": self._address,
                    "action": "on" if turn_on else "off",
                },
            )
        # A dedicated on or off key is idempotent and is always pressed.
        # A toggle key, or a start/stop key that would stop a running
        # loop, must not be pressed when the light is already where the
        # caller wants it. The check reads the state the entity shows —
        # the optimistic value when one is set, the polled image
        # otherwise — because the polled image alone can be stale for
        # as long as nothing refreshes it (push mode, or a linked plate
        # the button store does not know), and a stale image turned
        # "off" into a no-op and "on" into a toggle to off.
        if link.get("role") not in (ROLE_ON, ROLE_OFF) and self.is_on == turn_on:
            self._is_on = turn_on
            self.async_write_ha_state()
            return
        self._is_on = turn_on
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_button_press(str(link["bus_address"]))
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise command_error(err) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Press the key that switches the controller on."""
        await self._press(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Press the key that switches the controller off."""
        await self._press(False)
