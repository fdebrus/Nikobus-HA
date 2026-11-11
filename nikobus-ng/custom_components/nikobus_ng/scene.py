"""The central functions the link scan found, as scenes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from nikobus_connect.discovery.store import is_button_backed_cf

from .const import DIMMER_READ_DELAY
from .entity import NikobusEntity, gateway_device
from .hub import NikobusHub

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One scene per central function that is not a shutter group.

    A light scene on a wall key or an IR code is one only under the name
    the project file gives it; unnamed, it is that key, which has its own
    entities.
    """
    hub = entry.runtime_data
    async_add_entities(
        NikobusScene(hub, address, record, name)
        for address, record in sorted(hub.inventory.scenes.items())
        if (name := hub.inventory.scene_name(record)) or not is_button_backed_cf(record)
    )


class NikobusScene(NikobusEntity, Scene):
    """Activated on the bus the way its own key activates it: one telegram."""

    def __init__(self, hub: NikobusHub, address: str, record: dict[str, Any], name: str | None) -> None:
        super().__init__(hub)
        self._bus_address = str(record.get("bus_address") or address).upper()
        self._groups = sorted(
            {
                (str(output["module_address"]).upper(), 1 if int(output["channel"]) <= 6 else 2)
                for output in record.get("outputs") or []
            }
        )
        self._attr_unique_id = f"{address}_scene"
        if name:
            self._attr_name = name
        else:
            self._attr_translation_key = "central_function"
            self._attr_translation_placeholders = {"address": self._bus_address}
        self._attr_device_info = gateway_device(hub)
        self._attr_extra_state_attributes = {
            "bus_address": self._bus_address,
            "outputs": len(record.get("outputs") or []),
        }

    async def async_added_to_hass(self) -> None:
        """Follow the connection."""
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))

    async def async_activate(self, **kwargs: Any) -> None:
        """Send the central function's telegram, then read what it reached."""
        await self.hub.async_press(self._bus_address)
        for address, group in self._groups:
            self.hub.presses.schedule_read(address, group, DIMMER_READ_DELAY)
