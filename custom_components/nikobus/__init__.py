"""The Nikobus integration - Platinum Edition."""
from __future__ import annotations

import logging
from typing import Final

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.components import (
    switch,
    light,
    cover,
    binary_sensor,
    button,
    scene,
)

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final[list[str]] = [
    cover.DOMAIN,
    switch.DOMAIN,
    light.DOMAIN,
    binary_sensor.DOMAIN,
    button.DOMAIN,
    scene.DOMAIN,
]

SCAN_MODULE_SCHEMA = vol.Schema({vol.Optional("module_address", default=""): cv.string})
HUB_IDENTIFIER: Final[str] = "nikobus_hub"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Nikobus integration (single-instance) without redundant handshakes."""
    _LOGGER.debug("Starting setup of Nikobus (single-instance)")

    # 1. Initialize the coordinator immediately
    coordinator = NikobusDataCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # 2. Connect once. If this fails, the handshake or hardware is unavailable.
    try:
        await coordinator.connect()
    except Exception as err:
        _LOGGER.error("Could not establish Nikobus connection: %s", err)
        raise ConfigEntryNotReady(f"Nikobus hardware not responding: {err}") from err

    _register_hub_device(hass, entry)

    # 3. Register Discovery Service
    async def handle_module_discovery(call: ServiceCall) -> None:
        """Manually trigger device discovery via the coordinator's discovery engine."""
        module_address = (call.data.get("module_address", "") or "").strip().upper()
        _LOGGER.info("Starting manual Nikobus discovery for: %s", module_address or "All Modules")
        # Optimization: Use the discovery object directly
        if coordinator.nikobus_discovery:
            await coordinator.nikobus_discovery.start_discovery(module_address)

    if not hass.services.has_service(DOMAIN, "query_module_inventory"):
        hass.services.async_register(
            DOMAIN,
            "query_module_inventory",
            handle_module_discovery,
            SCAN_MODULE_SCHEMA,
        )

    # 4. Forward setup to platforms
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Successfully forwarded setup to Nikobus platforms")
    except Exception as err:
        _LOGGER.error("Error forwarding setup: %s", err)
        return False

    # 5. Clean up stale entities
    await _async_cleanup_orphan_entities(hass, entry, coordinator)

    _LOGGER.info("Nikobus integration setup complete.")
    return True

def _register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the Nikobus bridge (hub) as a device."""
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
    """Remove entities and devices that no longer exist in the Nikobus configuration."""
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
    """Unload the integration and stop background tasks."""
    coordinator = entry.runtime_data
    if coordinator:
        await coordinator.stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if hass.services.has_service(DOMAIN, "query_module_inventory"):
        hass.services.async_remove(DOMAIN, "query_module_inventory")

    return unload_ok