"""Media player platform: one entity per Audio Distribution zone.

An Audio Distribution module (05-205) is driven by virtual buttons, one
per zone and function, whose addresses discovery reads out of the module
itself (nikobus-connect 0.39.0). Pressing the address is the whole
protocol: there is no state query, so what a zone is doing is known only
from the commands seen on the bus — the ones Home Assistant sends and
the ones a wall key sends, both of which the PC-Link relays.

The entity is therefore optimistic, and corrects itself whenever another
source drives the same zone.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, press_signal
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity, command_error
from .router import (
    AUDIO_FUNCTION_OFF,
    AUDIO_FUNCTION_ON,
    AUDIO_FUNCTION_VOLUME_DOWN,
    AUDIO_FUNCTION_VOLUME_UP,
    AUDIO_SOURCE_FUNCTIONS,
    audio_zones,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one media player per audio zone."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})

    entities = [
        NikobusAudioZone(coordinator, module, zone, functions)
        for (module, zone), functions in sorted(audio_zones(buttons).items())
        if zone  # zone 0 collects the all-zones triggers; not a player
    ]
    async_add_entities(entities)


class NikobusAudioZone(NikobusEntity, MediaPlayerEntity, RestoreEntity):
    """One zone of an Audio Distribution module."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        module_address: str,
        zone: int,
        functions: dict[str, str],
    ) -> None:
        """Initialize the zone from the trigger addresses discovery found."""
        super().__init__(
            coordinator,
            module_address,
            coordinator.address_label(module_address) or f"Audio {module_address}",
            "05-205",
        )
        self._module_address = module_address
        self._zone = zone
        self._functions = functions
        self._attr_name = f"Zone {zone}"
        self._attr_unique_id = f"{DOMAIN}_audio_{module_address.lower()}_zone{zone}"
        self._attr_state: MediaPlayerState | None = None
        self._attr_source: str | None = None

        self._sources = {
            f"Source {index}": address
            for index, function in enumerate(AUDIO_SOURCE_FUNCTIONS, start=1)
            if (address := functions.get(function))
        }
        self._attr_source_list = list(self._sources)

        features = MediaPlayerEntityFeature(0)
        if AUDIO_FUNCTION_ON in functions:
            features |= MediaPlayerEntityFeature.TURN_ON
        if AUDIO_FUNCTION_OFF in functions:
            features |= MediaPlayerEntityFeature.TURN_OFF
        if AUDIO_FUNCTION_VOLUME_UP in functions or AUDIO_FUNCTION_VOLUME_DOWN in functions:
            features |= MediaPlayerEntityFeature.VOLUME_STEP
        if self._sources:
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        self._attr_supported_features = features

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the trigger addresses, so the wiring is inspectable."""
        parent = super().extra_state_attributes or {}
        return {
            **parent,
            "nikobus_address": self._module_address,
            "zone": self._zone,
            "trigger_addresses": dict(sorted(self._functions.items())),
        }

    @property
    def source_list(self) -> list[str]:
        """The sources this zone's module offers."""
        return list(self._sources)

    @property
    def source(self) -> str | None:
        """Last source commanded on this zone, if any."""
        return self._attr_source

    @property
    def state(self) -> MediaPlayerState | None:
        """Last known state; ``None`` until something drives the zone.

        The module answers no state query, so this is what was last
        commanded — by Home Assistant or by a wall key.
        """
        return self._attr_state

    async def async_added_to_hass(self) -> None:
        """Restore the last state and watch the zone's own addresses."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state in (MediaPlayerState.ON, MediaPlayerState.OFF):
                self._attr_state = MediaPlayerState(last.state)
            self._attr_source = last.attributes.get("source")
        for address in self._functions.values():
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, press_signal(address), self._handle_press
                )
            )

    @callback
    def _handle_press(self, payload: dict[str, Any] | None = None) -> None:
        """A trigger for this zone was seen on the bus — follow it.

        Fires for a wall key as well as for a press Home Assistant sent,
        so the two stay in step.
        """
        address = str((payload or {}).get("address") or "").upper()
        self._apply(address)
        self.async_write_ha_state()

    def _apply(self, address: str) -> None:
        for function, trigger in self._functions.items():
            if trigger != address:
                continue
            if function == AUDIO_FUNCTION_ON:
                self._attr_state = MediaPlayerState.ON
            elif function == AUDIO_FUNCTION_OFF:
                self._attr_state = MediaPlayerState.OFF
            elif function in AUDIO_SOURCE_FUNCTIONS:
                self._attr_state = MediaPlayerState.ON
                self._attr_source = next(
                    (name for name, addr in self._sources.items() if addr == address),
                    self._attr_source,
                )
            return

    async def _press(self, function: str) -> None:
        """Put the function's trigger on the bus."""
        address = self._functions.get(function)
        if address is None:
            raise command_error(RuntimeError(f"{function} is not programmed on this zone"))
        try:
            await self.coordinator.async_send_button_press(address)
        except Exception as err:  # noqa: BLE001 - surfaced as an HA error
            raise command_error(err) from err
        self._apply(address)
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Switch the zone on."""
        await self._press(AUDIO_FUNCTION_ON)

    async def async_turn_off(self) -> None:
        """Switch the zone off."""
        await self._press(AUDIO_FUNCTION_OFF)

    async def async_volume_up(self) -> None:
        """One step up."""
        await self._press(AUDIO_FUNCTION_VOLUME_UP)

    async def async_volume_down(self) -> None:
        """One step down."""
        await self._press(AUDIO_FUNCTION_VOLUME_DOWN)

    async def async_select_source(self, source: str) -> None:
        """Select one of the module's sources."""
        address = self._sources.get(source)
        if address is None:
            raise command_error(RuntimeError(f"unknown source {source!r}"))
        function = next(f for f, a in self._functions.items() if a == address)
        await self._press(function)
