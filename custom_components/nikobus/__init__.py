"""The Nikobus integration."""

import logging

from homeassistant import config_entries, core, exceptions
from homeassistant.core import HomeAssistant
from homeassistant.components import switch, light, cover, binary_sensor, button

from .const import DOMAIN
from .coordinator import NikobusDataCoordinator, NikobusConnectError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [switch.DOMAIN, light.DOMAIN, cover.DOMAIN, binary_sensor.DOMAIN, button.DOMAIN]

async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    _LOGGER.debug("Starting setup of the Nikobus integration.")

    coordinator = NikobusDataCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    try:
        await coordinator.connect()
    except NikobusConnectError as connect_error:
        _LOGGER.error(f"Failed to connect to Nikobus: {connect_error}")
        return False
    except exceptions.HomeAssistantError as ha_error:
        _LOGGER.error(f"An error occurred in the Home Assistant core while setting up Nikobus: {ha_error}")
        return False
    except Exception as unexpected_error:
        _LOGGER.error(f"An unexpected error occurred during Nikobus setup: {unexpected_error}")
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    try:
        await coordinator.async_config_entry_first_refresh()
    except exceptions.UpdateFailed:
        _LOGGER.error("Initial data refresh failed. Nikobus integration setup cannot continue.")
        return False

    return True
