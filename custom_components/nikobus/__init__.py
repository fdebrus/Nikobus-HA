"""The Nikobus integration."""
from __future__ import annotations

import logging
from typing import Final

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.components import (
    switch,
    light,
    cover,
    binary_sensor,
    button,
    scene,
)

from .nkbconnect import NikobusConnect
from .exceptions import NikobusConnectionError
from .const import DOMAIN, CONF_CONNECTION_STRING
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
    """Set up the Nikobus integration from a config entry (single-instance)."""
    _LOGGER.debug("Starting setup of Nikobus (single-instance)")

    try:
        connection = NikobusConnect(entry.data[CONF_CONNECTION_STRING])
        await connection.ping()
    except NikobusConnectionError as err:
        _LOGGER.warning("Nikobus interface not ready: %s", err)
        raise ConfigEntryNotReady from err

    # Create and store the coordinator (which may start the event listener)
    coordinator = NikobusDataCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # Attempt to connect the coordinator
    try:
        await coordinator.connect()
    except HomeAssistantError as err:
        _LOGGER.error("Error connecting to Nikobus: %s", err)
        raise ConfigEntryNotReady from err

    _register_hub_device(hass, entry)

    async def async_cleanup_orphan_entities() -> None:
        """Remove entities & devices that no longer exist in current Nikobus config."""
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        valid_entity_ids = coordinator.get_known_entity_unique_ids()
        _LOGGER.debug("Valid Nikobus entity IDs: %s", valid_entity_ids)

        # 1) Clean up entities
        for entity in list(ent_reg.entities.values()):
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.platform != DOMAIN:
                continue

            if entity.unique_id not in valid_entity_ids:
                _LOGGER.info(
                    "Removing orphan Nikobus entity: %s (unique_id=%s)",
                    entity.entity_id,
                    entity.unique_id,
                )
                ent_reg.async_remove(entity.entity_id)

        # 2) Clean up devices that have no remaining entities (but keep the hub device)
        hub_identifier = (DOMAIN, HUB_IDENTIFIER)

        # Rebuild after entity removals
        ent_reg = er.async_get(hass)

        devices_with_entities: set[str] = set()
        for entity in ent_reg.entities.values():
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.platform != DOMAIN:
                continue
            if entity.device_id:
                devices_with_entities.add(entity.device_id)

        for device in list(dev_reg.devices.values()):
            if entry.entry_id not in device.config_entries:
                continue

            if hub_identifier in device.identifiers:
                continue

            if device.id not in devices_with_entities:
                _LOGGER.info(
                    "Removing orphan Nikobus device: %s (id=%s, identifiers=%s)",
                    device.name,
                    device.id,
                    device.identifiers,
                )
                dev_reg.async_remove_device(device.id)

    async def handle_module_discovery(call: ServiceCall) -> None:
        """Manually trigger device discovery."""
        module_address = call.data.get("module_address", "")
        _LOGGER.info(
            "Starting manual Nikobus discovery with module_address: %s",
            module_address,
        )
        await coordinator.discover_devices(module_address)

    hass.services.async_register(
        DOMAIN, "query_module_inventory", handle_module_discovery, SCAN_MODULE_SCHEMA
    )

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Successfully forwarded setup to Nikobus platforms")
    except Exception as err:
        _LOGGER.error("Error forwarding setup to Nikobus platforms: %s", err)
        try:
            await coordinator.stop()
        except Exception as stop_err:
            _LOGGER.debug(
                "Error while stopping coordinator after forward failure: %s",
                stop_err,
            )
        return False

    await async_cleanup_orphan_entities()

    _LOGGER.info("Nikobus (single-instance) setup complete.")
    return True


def _register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the Nikobus bridge (hub) as a device in Home Assistant."""
    device_registry = dr.async_get(hass)
    hub_identifiers = {(DOMAIN, HUB_IDENTIFIER)}

    if device_registry.async_get_device(identifiers=hub_identifiers):
        _LOGGER.debug("Nikobus hub device already exists in registry.")
        return

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=hub_identifiers,
        manufacturer="Niko",
        name="Nikobus Bridge",
        model="PC-Link Bridge",
    )
    _LOGGER.debug("Nikobus hub registered in Home Assistant device registry.")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the single Nikobus integration entry."""
    _LOGGER.debug("Unloading Nikobus (single-instance)")

    domain_data = hass.data.get(DOMAIN)
    if domain_data:
        remove = domain_data.pop("button_sensor_remove", None)
        if remove:
            try:
                remove()
            except Exception as err:
                _LOGGER.error(
                    "Error removing Nikobus button sensor listener: %s", err
                )

    coordinator: NikobusDataCoordinator | None = entry.runtime_data

    if coordinator and hasattr(coordinator, "stop"):
        try:
            await coordinator.stop()
        except Exception as err:
            _LOGGER.error("Error stopping Nikobus coordinator: %s", err)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.error("Failed to unload Nikobus platforms.")
        return False

    _LOGGER.info("Nikobus integration fully unloaded.")
    return True
