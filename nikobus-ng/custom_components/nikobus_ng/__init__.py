"""Nikobus NG: a Nikobus installation in Home Assistant, discovered from the bus."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import PLATFORMS
from .entity import gateway_device
from .hub import NikobusHub
from .inventory import Inventory

type NikobusConfigEntry = ConfigEntry[NikobusHub]


async def async_setup_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Connect, create the entities of what is known, discover what is not."""
    hub = NikobusHub(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub
    hub.gateway_device_id = (
        dr.async_get(hass)
        .async_get_or_create(config_entry_id=entry.entry_id, **gateway_device(hub))
        .id
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.async_stop()
        raise
    if hub.inventory.is_empty:
        # Nothing is known yet: read the installation, then reload.
        hub.discovery.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Remove the entities and release the port."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> None:
    """Delete what discovery stored for the entry."""
    await Inventory(hass, entry.entry_id).async_remove()

