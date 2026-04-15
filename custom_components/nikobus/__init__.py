"""The Nikobus integration - Platinum Edition."""
from __future__ import annotations

import logging
from typing import Final

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.components import (
    switch,
    light,
    cover,
    binary_sensor,
    button,
    scene,
    sensor,
)

from .const import DOMAIN, HUB_IDENTIFIER
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .exceptions import NikobusConnectionError, NikobusDataError, NikobusError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final[list[str]] = [
    cover.DOMAIN,
    switch.DOMAIN,
    light.DOMAIN,
    binary_sensor.DOMAIN,
    button.DOMAIN,
    scene.DOMAIN,
    sensor.DOMAIN,
]

SCAN_MODULE_SCHEMA = vol.Schema({vol.Optional("module_address", default=""): cv.string})

async def async_setup_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Set up the Nikobus integration (single-instance) without redundant handshakes."""
    _LOGGER.debug("Starting setup of Nikobus (single-instance)")

    # 1. Initialize the coordinator
    # We create the object but ensure it doesn't broadcast data prematurely.
    coordinator = NikobusDataCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # 2. Connect and prepare internal buffers.
    try:
        await coordinator.connect()
    except NikobusConnectionError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Nikobus: {err}"
        ) from err
    except NikobusDataError as err:
        raise ConfigEntryNotReady(
            f"Check your Nikobus config files — {err}"
        ) from err
    except NikobusError as err:
        raise ConfigEntryNotReady(f"Nikobus setup error: {err}") from err

    _register_hub_device(hass, entry)

    # 3. Register Discovery Service
    async def handle_module_discovery(call: ServiceCall) -> None:
        """Manually trigger device discovery via the coordinator's discovery engine."""
        module_address = (call.data.get("module_address", "") or "").strip().upper()
        
        if not module_address:
            _LOGGER.info("Starting manual Nikobus PC-Link inventory discovery (#A)")
            coordinator._discovery_found_data = False
            coordinator._consecutive_empty_blocks = 0
            if coordinator.nikobus_discovery:
                await coordinator.nikobus_discovery.start_inventory_discovery()
        else:
            _LOGGER.info("Starting manual Nikobus discovery for module: %s", module_address)
            if coordinator.nikobus_discovery:
                await coordinator.nikobus_discovery.query_module_inventory(module_address)

    if not hass.services.has_service(DOMAIN, "query_module_inventory"):
        hass.services.async_register(
            DOMAIN,
            "query_module_inventory",
            handle_module_discovery,
            SCAN_MODULE_SCHEMA,
        )

    # 4. Forward setup to platforms FIRST
    # This allows entities to be created and register their dispatcher listeners.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 5. Reload when the user changes options via the OptionsFlow
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # 6. Trigger the first refresh
    # Since platforms are loaded, entities will correctly receive the "Targeted update signal".
    _LOGGER.debug("Performing initial Nikobus data synchronization")
    await coordinator.async_config_entry_first_refresh()

    # 7. Clean up stale entities
    await _async_cleanup_orphan_entities(hass, entry, coordinator)

    _LOGGER.info("Nikobus integration setup complete.")
    return True


async def _async_options_updated(hass: HomeAssistant, entry: NikobusConfigEntry) -> None:
    """Reload the integration when the user changes options."""
    await hass.config_entries.async_reload(entry.entry_id)


def _register_hub_device(hass: HomeAssistant, entry: NikobusConfigEntry) -> None:
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
    hass: HomeAssistant, entry: NikobusConfigEntry, coordinator: NikobusDataCoordinator
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

async def async_unload_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Unload the integration and stop background tasks."""
    coordinator = entry.runtime_data
    if coordinator:
        await coordinator.stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if hass.services.has_service(DOMAIN, "query_module_inventory"):
        hass.services.async_remove(DOMAIN, "query_module_inventory")

    return unload_ok