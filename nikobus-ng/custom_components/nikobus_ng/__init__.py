"""Nikobus NG: a Nikobus installation in Home Assistant, discovered from the bus."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .entity import gateway_device
from .hub import NikobusHub
from .inventory import Inventory
from .services import async_setup_services

type NikobusConfigEntry = ConfigEntry[NikobusHub]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    async_setup_services(hass)
    return True


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
    inventory = hub.inventory
    if inventory.is_empty:
        # Nothing is known yet: read the installation, then reload.
        hub.discovery.start()
        return True
    _remove_devices_no_longer_known(hass, entry, hub)
    if inventory.from_project and not any(inventory.links_read(m.address) for m in inventory.output_modules()):
        # A project file named the modules; which key drives what is still in them.
        hub.discovery.start()
    elif retyped := inventory.types_to_read_again():
        # A module the options gave another type: its links are decoded by type.
        hub.discovery.start(retyped[0])
    return True


def _remove_devices_no_longer_known(hass: HomeAssistant, entry: NikobusConfigEntry, hub: NikobusHub) -> None:
    """Delete the devices of modules and plates the inventory lost."""
    known = {entry.entry_id, *hub.inventory.addresses()}
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if not any(domain == DOMAIN and identifier in known for domain, identifier in device.identifiers):
            devices.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NikobusConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Deleting a module or a plate forgets it; the gateway stays."""
    addresses = [identifier for domain, identifier in device.identifiers if domain == DOMAIN]
    if entry.entry_id in addresses:
        return False
    for address in addresses:
        await entry.runtime_data.async_forget(address)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Remove the entities and release the port."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> None:
    """Delete what discovery stored for the entry."""
    await Inventory(hass, entry.entry_id).async_remove()

