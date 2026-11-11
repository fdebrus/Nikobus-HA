"""The outputs of a dimmer module."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from nikobus_connect.rgb import (
    RGB_OFF_ROLES,
    RGB_ON_ROLES,
    ROLE_OFF,
    ROLE_ON,
    RgbState,
    decode_rgb_state,
)

from .const import DIMMER_MODULE, DOMAIN, MANUFACTURER
from .entity import ChannelEntity, NikobusEntity, OnOffEntity, outputs_of
from .hub import NikobusHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A light per dimmer output, and per relay or roller output the options show as one."""
    hub = entry.runtime_data
    async_add_entities(
        (NikobusLight if module.module_type == DIMMER_MODULE else NikobusOnOffLight)(hub, module, channel)
        for module, channel in outputs_of(hub, Platform.LIGHT)
    )
    async_add_entities(NikobusRgbLight(hub, address, entry) for address, entry in hub.inventory.rgb_modules())


class NikobusLight(ChannelEntity, LightEntity):
    """A dimmed output; its byte is its level."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        """Whether the level is above zero."""
        value = self._value
        return None if value is None else value > 0

    @property
    def brightness(self) -> int | None:
        """The level, 0-255."""
        return self._value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the level; full when none is given."""
        level = max(1, min(255, int(kwargs.get(ATTR_BRIGHTNESS, 255))))
        await self.hub.async_set_output(self._address, self._channel, level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the level to zero."""
        await self.hub.async_set_output(self._address, self._channel, 0x00)


class NikobusOnOffLight(OnOffEntity, LightEntity):
    """A relay that switches a lamp."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}


class NikobusRgbLight(NikobusEntity, LightEntity):
    """A colour controller: on or off, with the colour its lit outputs mix to.

    The controller reports one on flag and one lit-or-not flag per
    colour, takes no set-output and has no levels. It is switched by
    pressing a key linked to it, and which keys are is known from the
    project file only: without one, or without a key that switches, the
    light is a readout and says so when asked to switch.
    """

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}
    _attr_name = None

    def __init__(self, hub: NikobusHub, address: str, entry: dict[str, Any]) -> None:
        super().__init__(hub)
        self._address = address
        links = entry.get("rgb_links")
        self._links: list[dict[str, Any]] = links if isinstance(links, list) else []
        self._attr_unique_id = f"{address}_rgb"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            manufacturer=MANUFACTURER,
            model=str(entry.get("model") or "") or None,
            name=hub.inventory.device_name(address) or f"RGB controller {address}",
            suggested_area=hub.inventory.device_area(address),
            via_device_id=hub.gateway_device_id,
        )

    async def async_added_to_hass(self) -> None:
        """Follow the controller's state."""
        self.async_on_remove(self.hub.subscribe(self._address, self.async_write_ha_state))

    def _state(self) -> RgbState | None:
        image = self.hub.image(self._address, 1)
        return decode_rgb_state(image) if image is not None else None

    def _key(self, roles: tuple[str, ...]) -> dict[str, Any] | None:
        """The linked key whose role comes first in ``roles``."""
        ranked = [link for link in self._links if link.get("role") in roles and link.get("bus_address")]
        return min(ranked, key=lambda link: roles.index(link["role"])) if ranked else None

    @property
    def is_on(self) -> bool | None:
        """The controller's on flag; unknown until it was read."""
        state = self._state()
        return state.on if state is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The colour, and the keys that switch the controller."""
        state = self._state()
        return {
            "colour": state.colour_name if state is not None else None,
            "on_key": (self._key(RGB_ON_ROLES) or {}).get("bus_address"),
            "off_key": (self._key(RGB_OFF_ROLES) or {}).get("bus_address"),
        }

    async def _async_switch(self, on: bool) -> None:
        link = self._key(RGB_ON_ROLES if on else RGB_OFF_ROLES)
        if link is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rgb_no_key",
                translation_placeholders={"address": self._address},
            )
        # A key that only switches on or off can always be pressed. Any
        # other (a toggle, a start/stop) must not be when the controller
        # is already there, so the controller is read first.
        if link["role"] not in (ROLE_ON, ROLE_OFF):
            await self.hub.async_read_group(self._address, 1)
            if self.is_on is on:
                return
        await self.hub.async_press(str(link["bus_address"]))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Press the key that switches the controller on."""
        await self._async_switch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Press the key that switches the controller off."""
        await self._async_switch(False)
