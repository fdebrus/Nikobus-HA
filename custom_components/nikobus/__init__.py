"""The Nikobus integration."""
from __future__ import annotations

import logging
from typing import Final
import datetime
import voluptuous as vol
import asyncio

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.components import (
    switch,
    light,
    cover,
    binary_sensor,
    button,
    scene,
)
from homeassistant.helpers.event import async_track_time_change
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

    async def async_cleanup_orphan_entities(hass, entry, coordinator):
        """Remove entities that no longer exist in current Nikobus config."""
        ent_reg = er.async_get(hass)
        valid = coordinator.get_known_entity_unique_ids()
        _LOGGER.debug("Valid Nikobus entity IDs: %s", valid)

        for entity in list(ent_reg.entities.values()):
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.platform != DOMAIN:
                continue

            if entity.unique_id not in valid:
                _LOGGER.warning(
                    "Removing orphan Nikobus entity: %s (unique_id=%s)",
                    entity.entity_id,
                    entity.unique_id,
                )
                ent_reg.async_remove(entity.entity_id)

    async def handle_module_discovery(call: ServiceCall) -> None:
        """Manually trigger device discovery."""
        module_address = call.data.get("module_address", "")
        _LOGGER.info("Starting manual Nikobus discovery with module_address: %s", module_address)
        await coordinator.discover_devices(module_address)

    hass.services.async_register(
        DOMAIN, "query_module_inventory", handle_module_discovery, SCAN_MODULE_SCHEMA
    )

    async def scheduled_discovery(now: datetime) -> None:
        _LOGGER.info("Scheduled Nikobus discovery running at: %s", now)
        await coordinator.discover_devices("")

    # Schedule the callback to run daily at 1:00:00 AM.
    # remove_listener = async_track_time_change(
    #     hass,
    #    lambda now: asyncio.run_coroutine_threadsafe(scheduled_discovery(now), hass.loop),
    #     hour=10,
    #     minute=0,
    #     second=0
    # )
    # Store the remove_listener so that it can be cancelled when unloading the integration.
    # coordinator.remove_listener = remove_listener

    # Forward the setup to all configured platforms.
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Successfully forwarded setup to Nikobus platforms")
    except Exception as err:
        _LOGGER.error("Error forwarding setup to Nikobus platforms: %s", err)
        return False

    await async_cleanup_orphan_entities(hass, entry, coordinator)

    _LOGGER.info("Nikobus (single-instance) setup complete.")
    return True

def _register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the Nikobus bridge (hub) as a device in Home Assistant."""
    device_registry = dr.async_get(hass)
    if device_registry.async_get_device(identifiers={(DOMAIN, HUB_IDENTIFIER)}):
        _LOGGER.debug("Nikobus hub device already exists in registry.")
        return

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        manufacturer="Niko",
        name="Nikobus Bridge",
        model="PC-Link Bridge",
    )
    _LOGGER.debug("Nikobus hub registered in Home Assistant device registry.")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the single Nikobus integration entry."""
    _LOGGER.debug("Unloading Nikobus (single-instance)")
    coordinator = entry.runtime_data

    # Cancel the scheduled discovery if it exists.
    if hasattr(coordinator, "remove_listener"):
        coordinator.remove_listener()

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
