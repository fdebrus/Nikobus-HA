"""The zones of an audio distribution module.

The module is driven by frames, one per zone and function, which
discovery reads out of the module itself. It answers no state query, so
a zone's state is what was last sent: by Home Assistant, or by a wall
key, whose frame the gateway relays.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, GROUP_SYSTEM_MODULES, MANUFACTURER
from .entity import NikobusEntity
from .hub import NikobusHub
from .presses import PRESSED

PARALLEL_UPDATES = 1

# The function labels the library's audio decoder gives a link record.
ON, OFF = "M16 (On)", "M17 (Off)"
VOLUME_UP, VOLUME_DOWN = "M13 (Volume up)", "M14 (Volume down)"
SOURCES = tuple(f"M{mode:02d} (Source {index})" for index, mode in enumerate(range(3, 11), start=1))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One media player per zone."""
    hub = entry.runtime_data
    async_add_entities(
        NikobusAudioZone(hub, module, zone, functions)
        for (module, zone), functions in sorted(hub.inventory.audio_zones().items())
    )


class NikobusAudioZone(NikobusEntity, MediaPlayerEntity, RestoreEntity):
    """One zone: on, off, volume steps and the source, as far as each is programmed."""

    _attr_translation_key = "zone"

    def __init__(self, hub: NikobusHub, module: str, zone: int, functions: dict[str, str]) -> None:
        super().__init__(hub)
        self._functions = functions
        self._by_frame = {frame: function for function, frame in functions.items()}
        self._sources = {
            f"Source {index}": function
            for index, function in enumerate(SOURCES, start=1)
            if function in functions
        }
        self._attr_unique_id = f"{module}_zone_{zone}"
        self._attr_translation_placeholders = {"zone": str(zone)}
        self._attr_source_list = list(self._sources)
        self._attr_state: MediaPlayerState | None = None
        self._attr_extra_state_attributes = {"frames": dict(sorted(functions.items()))}
        features = MediaPlayerEntityFeature(0)
        for needed, feature in (
            ((ON,), MediaPlayerEntityFeature.TURN_ON),
            ((OFF,), MediaPlayerEntityFeature.TURN_OFF),
            ((VOLUME_UP, VOLUME_DOWN), MediaPlayerEntityFeature.VOLUME_STEP),
            (SOURCES, MediaPlayerEntityFeature.SELECT_SOURCE),
        ):
            if any(function in functions for function in needed):
                features |= feature
        self._attr_supported_features = features
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module)},
            manufacturer=MANUFACTURER,
            model="05-205",
            name=hub.inventory.device_name(module) or f"Audio distribution {module}",
            suggested_area=hub.inventory.device_area(module),
            via_device_id=hub.group_device_id(GROUP_SYSTEM_MODULES),
        )

    async def async_added_to_hass(self) -> None:
        """Take the last state back, and follow the zone's frames on the bus."""
        if (last := await self.async_get_last_state()) is not None:
            if last.state in (MediaPlayerState.ON, MediaPlayerState.OFF):
                self._attr_state = MediaPlayerState(last.state)
            self._attr_source = last.attributes.get("source")
        for frame in self._by_frame:
            self.async_on_remove(self.hub.presses.subscribe(frame, self._seen(frame)))
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))

    def _seen(self, frame: str) -> Any:
        def _on_press(event_type: str, _data: dict[str, Any]) -> None:
            if event_type == PRESSED:
                self._apply(self._by_frame[frame])
                self.async_write_ha_state()

        return _on_press

    def _apply(self, function: str) -> None:
        if function == OFF:
            self._attr_state = MediaPlayerState.OFF
        elif function == ON or function in SOURCES:
            self._attr_state = MediaPlayerState.ON
        if function in SOURCES:
            self._attr_source = f"Source {SOURCES.index(function) + 1}"

    async def _async_send(self, function: str) -> None:
        if (frame := self._functions.get(function)) is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="zone_function_missing")
        await self.hub.async_press(frame)
        self._apply(function)
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Switch the zone on."""
        await self._async_send(ON)

    async def async_turn_off(self) -> None:
        """Switch the zone off."""
        await self._async_send(OFF)

    async def async_volume_up(self) -> None:
        """One step up."""
        await self._async_send(VOLUME_UP)

    async def async_volume_down(self) -> None:
        """One step down."""
        await self._async_send(VOLUME_DOWN)

    async def async_select_source(self, source: str) -> None:
        """Select one of the module's sources."""
        await self._async_send(self._sources[source])
