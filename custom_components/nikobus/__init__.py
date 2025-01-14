"""The Nikobus integration (single-instance)."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.components import (
    switch,
    light,
    cover,
    binary_sensor,
    button,
    scene,
)
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    switch.DOMAIN,
    light.DOMAIN,
    cover.DOMAIN,
    binary_sensor.DOMAIN,
    button.DOMAIN,
    scene.DOMAIN,
]

HUB_IDENTIFIER = "nikobus_hub"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up the Nikobus integration from a config entry (single-instance).
    """
    _LOGGER.debug("Starting setup of Nikobus (single-instance)")

    # Initialize the domain data structure
    hass.data.setdefault(DOMAIN, {})

    # Create the coordinator and store it in hass.data
    coordinator = NikobusDataCoordinator(hass, entry)
    hass.data[DOMAIN]["coordinator"] = coordinator

    # Attempt to connect the coordinator
    try:
        await coordinator.connect()
    except HomeAssistantError as err:
        _LOGGER.error("Error connecting to Nikobus: %s", err)
        raise ConfigEntryNotReady(f"Cannot connect to Nikobus: {err}") from err

    _register_hub_device(hass, entry)

    # Forward the setup to the appropriate platforms
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Successfully forwarded setup to Nikobus platforms")
    except Exception as err:
        _LOGGER.error("Error forwarding setup to Nikobus platforms: %s", err)
        raise ConfigEntryNotReady(
            f"Error setting up Nikobus platforms: {err}"
        ) from err

    # Add an update listener to handle configuration updates
    entry.add_update_listener(async_update_options)

    _LOGGER.info("Nikobus (single-instance) setup complete.")
    return True


def _register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Register a Nikobus device in Home Assistant's device registry.
    
    This ensures we have a device entry that can be referenced by entities,
    diagnostics, etc.
    """
    device_registry = dr.async_get(hass)

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        manufacturer="Niko",
        name="Nikobus Bridge",
        model="PC-Link Bridge",
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload the single Nikobus integration entry.
    """
    _LOGGER.debug("Unloading Nikobus (single-instance)")

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.error("Failed to unload Nikobus platforms.")
        return False

    # Remove the coordinator from hass.data
    hass.data.pop(DOMAIN, None)

    _LOGGER.info("Nikobus integration unloaded successfully.")
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update for the single-instance Nikobus integration."""
    _LOGGER.debug("Updating Nikobus options")

    coordinator = hass.data[DOMAIN].get("coordinator")
    if not coordinator:
        _LOGGER.error("Coordinator not found in domain data.")
        return

    await coordinator.async_config_entry_updated(entry)
    _LOGGER.info("Nikobus options updated.")
