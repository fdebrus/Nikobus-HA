"""The Nikobus integration - Platinum Edition."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.components import (
    binary_sensor,
    button,
    cover,
    light,
    scene,
    switch,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS: Final[list[Platform]] = [
    Platform.COVER,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SCENE,
]

HUB_IDENTIFIER: Final[str] = "nikobus_hub"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Nikobus integration using the nikobusconnect library."""
    _LOGGER.debug("Starting setup of Nikobus (Platinum Edition)")

    # 1. Initialize the coordinator
    # This now handles the nikobusconnect==1.0.0 library initialization
    coordinator = NikobusDataCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # 2. Connect and load local JSON configurations
    try:
        await coordinator.connect()
    except Exception as err:
        _LOGGER.error("Could not establish Nikobus connection: %s", err)
        raise ConfigEntryNotReady(f"Nikobus hardware not responding: {err}") from err

    # 3. Register the central PC-Link bridge device
    _register_hub_device(hass, entry)

    # 4. Forward setup to platforms
    # Entities will load based on your local nikobus_*.json files
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Successfully forwarded setup to Nikobus platforms")
    except Exception as err:
        _LOGGER.error("Error forwarding setup: %s", err)
        return False

    # 5. Trigger the first data refresh
    # This polls the modules defined in your config and updates entity states
    _LOGGER.debug("Performing initial Nikobus data synchronization")
    await coordinator.async_config_entry_first_refresh()

    # 6. Clean up stale entities (orphans from changed JSON files)
    await _async_cleanup_orphan_entities(hass, entry, coordinator)

    _LOGGER.info("Nikobus integration setup complete.")
    return True


def _register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the Nikobus bridge (hub) as a device in the registry."""
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        manufacturer="Niko",
        name="Nikobus Bridge",
        model="PC-Link Bridge",
    )


async def _async_cleanup_orphan_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: NikobusDataCoordinator
) -> None:
    """Remove entities and devices that no longer exist in the local JSON configuration."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    valid_entity_ids = coordinator.get_known_entity_unique_ids()
    
    # Remove orphan entities
    entities = [
        entity for entity in ent_reg.entities.values()
        if entity.config_entry_id == entry.entry_id and entity.platform == DOMAIN
    ]
    for entity in entities:
        if entity.unique_id not in valid_entity_ids:
            ent_reg.async_remove(entity.entity_id)

    # Remove orphan devices (except the hub)
    hub_identifier = (DOMAIN, HUB_IDENTIFIER)
    devices_with_entities = {
        entity.device_id for entity in ent_reg.entities.values()
        if entity.config_entry_id == entry.entry_id and entity.device_id
    }

    for device in list(dev_reg.devices.values()):
        if entry.entry_id in device.config_entries and hub_identifier not in device.identifiers:
            if device.id not in devices_with_entities:
                dev_reg.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration and stop the library listener/connection."""
    coordinator = entry.runtime_data
    if coordinator:
        await coordinator.stop()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)