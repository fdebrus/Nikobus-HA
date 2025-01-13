"""Nikobus Integration"""

import logging
from homeassistant.core import HomeAssistant
from homeassistant.components import (
    switch,
    light,
    cover,
    binary_sensor,
    button,
    scene,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, CONF_CONNECTION_STRING, CONF_HAS_FEEDBACK_MODULE, CONF_REFRESH_INTERVAL
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Nikobus integration from a config entry."""
    _LOGGER.info("Starting setup of the Nikobus integration")

    # Ensure hass.data[DOMAIN] exists
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Store the entry in hass.data to prevent KeyError on unload
    hass.data[DOMAIN][entry.entry_id] = entry

    # Initialize the coordinator
    coordinator = NikobusDataCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register the device
    device_registry = async_get_device_registry(hass)
    nikobus_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Niko",
        name="Nikobus Controller",
        model="Nikobus Bridge",
        sw_version="1.0",
    )

    _LOGGER.debug("Nikobus device registered: %s", nikobus_device)

    # Listen for config entry updates
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Attempt to connect the coordinator
    try:
        await coordinator.connect()
    except HomeAssistantError as e:
        _LOGGER.error("Error during connection setup: %s", e)
        raise HomeAssistantError(f"An error occurred loading configuration: {e}") from e

    # Forward setup to appropriate platforms
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as forward_setup_error:
        _LOGGER.error("Error while forwarding entry setups: %s", forward_setup_error)
        return False

    _LOGGER.info("Nikobus integration setup completed successfully")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    _LOGGER.info("Unloading Nikobus integration")

    # Remove devices from HA registry
    device_registry = async_get_device_registry(hass)
    for device in list(device_registry.devices.values()):
        if DOMAIN in device.identifiers:
            _LOGGER.debug("Removing Nikobus device: %s", device.name)
            device_registry.async_remove_device(device.id)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove stored data safely
    if unload_ok:
        if entry.entry_id in hass.data.get(DOMAIN, {}):
            _LOGGER.debug("Removing Nikobus entry from hass.data: %s", entry.entry_id)
            hass.data[DOMAIN].pop(entry.entry_id)
        else:
            _LOGGER.warning("Nikobus entry not found in hass.data during unload")

    _LOGGER.info("Nikobus integration unloaded: %s", "Success" if unload_ok else "Failed")
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update for the Nikobus integration."""
    _LOGGER.info("Updating Nikobus integration options")

    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_config_entry_updated(entry)
        _LOGGER.info("Nikobus integration options updated")
    else:
        _LOGGER.warning("Nikobus coordinator not found during options update")
