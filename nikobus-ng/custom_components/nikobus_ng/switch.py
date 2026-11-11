"""The outputs of a switch module, and the inputs of a PC-Logic or an interface."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import NikobusEntity, OnOffEntity, outputs_of, plate_device
from .hub import NikobusHub
from .presses import PRESSED

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A switch per relay not shown as a light, per roller output used as one, per logical input."""
    hub = entry.runtime_data
    async_add_entities(
        NikobusSwitch(hub, module, channel) for module, channel in outputs_of(hub, Platform.SWITCH)
    )
    async_add_entities(
        NikobusInputSwitch(hub, plate, plate_entry, on_frame, off_frame)
        for plate, plate_entry, on_frame, off_frame in hub.inventory.logical_inputs()
    )


class NikobusSwitch(OnOffEntity, SwitchEntity):
    """A relay."""


class NikobusInputSwitch(NikobusEntity, SwitchEntity, RestoreEntity):
    """A logical input, with the level its two frames stand for.

    The input holds no level on the bus: it sends one frame when it
    closes and another when it opens. The switch is on after the first
    and off after the second, whoever sent them, and sends them itself
    when it is turned on or off. Its state cannot be read back, so it is
    restored at a restart and may be behind until the next frame.
    """

    _attr_translation_key = "input"

    def __init__(
        self, hub: NikobusHub, plate: str, plate_entry: dict[str, Any], on_frame: str, off_frame: str
    ) -> None:
        super().__init__(hub)
        self._frames = {on_frame: True, off_frame: False}
        self._on_frame = on_frame
        self._off_frame = off_frame
        self._attr_is_on = False
        self._attr_unique_id = f"{plate}_input"
        self._attr_device_info = plate_device(hub, plate, plate_entry)
        self._attr_extra_state_attributes = {"on_address": on_frame, "off_address": off_frame}

    async def async_added_to_hass(self) -> None:
        """Take the last level back, and follow the two frames."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        for frame in self._frames:
            self.async_on_remove(self.hub.presses.subscribe(frame, self._on_frame_seen(frame)))
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))

    def _on_frame_seen(self, frame: str) -> Any:
        def _seen(event_type: str, _data: dict[str, Any]) -> None:
            if event_type == PRESSED:
                self._attr_is_on = self._frames[frame]
                self.async_write_ha_state()

        return _seen

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the frame of the closing input."""
        await self.hub.async_press(self._on_frame)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the frame of the opening input."""
        await self.hub.async_press(self._off_frame)
        self._attr_is_on = False
        self.async_write_ha_state()
