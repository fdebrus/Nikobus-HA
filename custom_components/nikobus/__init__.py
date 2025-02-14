"""The Nikobus integration."""

from __future__ import annotations

import logging
from typing import Final
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
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
    switch.DOMAIN,
    light.DOMAIN,
    cover.DOMAIN,
    binary_sensor.DOMAIN,
    button.DOMAIN,
    scene.DOMAIN,
]

SCAN_MODULE_SCHEMA = vol.Schema(
    {
        vol.Optional("module_address", default=""): cv.string,
    }
)

HUB_IDENTIFIER: Final[str] = "nikobus_hub"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Nikobus integration from a config entry (single-instance)."""
    _LOGGER.debug("Starting setup of Nikobus (single-instance)")

    # Create and store the coordinator
    coordinator = NikobusDataCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # Attempt to connect the coordinator
    try:
        await coordinator.connect()
    except HomeAssistantError as err:
        _LOGGER.error("Error connecting to Nikobus: %s", err)
        raise ConfigEntryNotReady from err

    _register_hub_device(hass, entry)

    async def handle_module_discovery(call: ServiceCall):
        """Manually trigger device discovery."""
        module_address = call.data.get("module_address", "")
        _LOGGER.info(
            f"Starting manual Nikobus discovery with module_address: {module_address}"
        )
        await coordinator.discover_devices(module_address)
        _LOGGER.info("Nikobus discovery completed")

    hass.services.async_register(
        DOMAIN, "query_module_inventory", handle_module_discovery, SCAN_MODULE_SCHEMA
    )

    # Forward the setup to all configured platforms
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Successfully forwarded setup to Nikobus platforms")
    except Exception as err:
        _LOGGER.error("Error forwarding setup to Nikobus platforms: %s", err)
        return False

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

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.error("Failed to unload Nikobus platforms.")
        return False

    _LOGGER.info("Nikobus integration fully unloaded.")
    return True
