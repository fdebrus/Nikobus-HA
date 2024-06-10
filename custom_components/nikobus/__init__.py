"""Nikobus Init"""

import logging
import json

from homeassistant import config_entries, core, exceptions
from homeassistant.core import HomeAssistant
from homeassistant.components import switch, light, cover, binary_sensor, button
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator, NikobusConnectError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, binary_sensor.DOMAIN, button.DOMAIN]

async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    _LOGGER.debug("Starting setup of the Nikobus integration")

    coordinator = NikobusDataCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    try:
        await coordinator.connect()
    except FileNotFoundError as file_error:
        _LOGGER.error(f"Configuration file not found: {file_error}")
        return False
    except json.JSONDecodeError as json_error:
        _LOGGER.error(f"Failed to decode configuration JSON: {json_error}")
        return False
    except NikobusConnectError as connect_error:
        _LOGGER.error(f"Failed to connect to Nikobus: {connect_error}")
        return False
    except exceptions.HomeAssistantError as ha_error:
        _LOGGER.error(f"An error occurred in the Home Assistant core while setting up Nikobus: {ha_error}")
        return False
    except Exception as unexpected_error:
        _LOGGER.error(f"An unexpected error occurred during Nikobus setup: {unexpected_error}")
        return False

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as forward_setup_error:
        _LOGGER.error(f"An error occurred while forwarding entry setups: {forward_setup_error}")
        return False

    # try:
    #    await coordinator.initial_update_data()
    # except UpdateFailed as update_failed_error:
    #    _LOGGER.error(f"Initial data refresh failed: {update_failed_error}")
    #    return False

    return True
