"""The gateway's buttons, and a button per key that presses it."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from nikobus_connect.discovery import is_motion_detector

from .entity import GatewayEntity, NikobusEntity, plate_device
from .hub import NikobusHub
from .inventory import Key

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The gateway's button, and the keys' press buttons (not enabled)."""
    hub = entry.runtime_data
    async_add_entities(
        [
            NikobusDiscoverButton(hub),
            NikobusMaintenanceButton(hub, "verify_programming"),
            NikobusMaintenanceButton(hub, "backup_programming"),
        ]
    )
    if hub.maintenance.pc_link_address is not None:
        async_add_entities([NikobusMaintenanceButton(hub, "set_clock")])
    async_add_entities(
        NikobusKeyButton(hub, key)
        for key in hub.inventory.iter_keys()
        if not is_motion_detector(key.plate_entry)
    )


class NikobusDiscoverButton(GatewayEntity, ButtonEntity):
    """Reads the inventory and every module's links, then reloads."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: NikobusHub) -> None:
        super().__init__(hub, "discover")

    @property
    def available(self) -> bool:
        """Not while a discovery or a maintenance run has the bus."""
        return self.hub.connected and not self.hub.busy

    async def async_press(self) -> None:
        """Start a discovery in the background."""
        self.hub.discovery.start()


class NikobusMaintenanceButton(GatewayEntity, ButtonEntity):
    """Sets the PC-Link's clock, checks the modules' programming, or backs it up.

    A check or a backup reads every module's memory, which takes minutes:
    it runs in the background and its result is on the programming sensor.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: NikobusHub, key: str) -> None:
        super().__init__(hub, key)
        self._key = key

    @property
    def available(self) -> bool:
        """Not while a discovery or a maintenance run has the bus."""
        return self.hub.connected and not self.hub.busy

    async def async_press(self) -> None:
        """Set the clock now; start a check or a backup in the background."""
        maintenance = self.hub.maintenance
        if self._key == "set_clock":
            await maintenance.async_set_clock()
            return
        maintenance.start(backup=self._key == "backup_programming")


class NikobusKeyButton(NikobusEntity, ButtonEntity):
    """Presses a key from Home Assistant; the bus cannot tell the difference."""

    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "press_key"

    def __init__(self, hub: NikobusHub, key: Key) -> None:
        super().__init__(hub)
        self._bus_address = key.bus_address
        self._attr_unique_id = f"{key.bus_address}_press"
        self._attr_translation_placeholders = {"key": key.label}
        self._attr_device_info = plate_device(hub, key.plate, key.plate_entry)

    async def async_added_to_hass(self) -> None:
        """Follow the connection."""
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))

    async def async_press(self) -> None:
        """Put the key's telegram on the bus."""
        await self.hub.async_press(self._bus_address)
