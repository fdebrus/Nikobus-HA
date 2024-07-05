"""Nikobus Init"""

import logging
import json

from homeassistant.core import HomeAssistant
from homeassistant.components import switch, light, cover, binary_sensor, button
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, CONF_HAS_FEEDBACK_MODULE
from .coordinator import NikobusDataCoordinator, NikobusConnectError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, binary_sensor.DOMAIN, button.DOMAIN]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug("Starting setup of the Nikobus integration")

    coordinator = NikobusDataCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    entry.add_update_listener(async_update_options)

    try:
        await coordinator.connect()
    except HomeAssistantError as e:
        raise HomeAssistantError(f'An error occurred loading configuration files: {e}')

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as forward_setup_error:
        _LOGGER.error(f"An error occurred while forwarding entry setups: {forward_setup_error}")
        return False

    has_feedback_module = entry.options.get(CONF_HAS_FEEDBACK_MODULE, entry.data.get(CONF_HAS_FEEDBACK_MODULE, False))

    if not has_feedback_module:
        try:
            await coordinator.initial_update_data()
        except UpdateFailed as update_failed_error:
            _LOGGER.error(f"Initial data refresh failed: {update_failed_error}")
            return False
        
    return True

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_config_entry_updated(entry)
