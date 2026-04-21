"""The Nikobus integration."""
from __future__ import annotations

import json
import logging
import os
from typing import Final

import voluptuous as vol
from aiofiles import open as aio_open

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
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

SERVICE_QUERY_MODULE_INVENTORY: Final = "query_module_inventory"
SERVICE_SEND_BUTTON_PRESS: Final = "send_button_press"
SCAN_MODULE_SCHEMA = vol.Schema({vol.Optional("module_address", default=""): cv.string})
SEND_BUTTON_PRESS_SCHEMA = vol.Schema({
    vol.Required("address"): cv.string,
})

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services."""

    async def handle_module_discovery(call: ServiceCall) -> None:
        """Trigger a Nikobus inventory scan via the coordinator's discovery engine."""
        module_address = (call.data.get("module_address", "") or "").strip().upper()

        coordinator = _loaded_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_loaded_entry",
            )
        if coordinator.nikobus_discovery is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="discovery_not_initialized",
            )
        if coordinator.discovery_running:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="discovery_already_running",
            )

        if not module_address:
            _LOGGER.info("Starting manual Nikobus PC-Link inventory discovery")
            coordinator.reset_discovery_counters()
            await coordinator.nikobus_discovery.start_inventory_discovery()
        else:
            _LOGGER.info("Starting manual Nikobus discovery for module: %s", module_address)
            await coordinator.nikobus_discovery.query_module_inventory(module_address)

    hass.services.async_register(
        DOMAIN,
        SERVICE_QUERY_MODULE_INVENTORY,
        handle_module_discovery,
        SCAN_MODULE_SCHEMA,
    )

    async def handle_send_button_press(call: ServiceCall) -> None:
        """Fire a Nikobus button-press command on the bus for the given address.

        Useful for virtual / IR-scene entries that are not part of discovery —
        define them as scripts/automations calling this service.
        """
        address = (call.data.get("address", "") or "").strip().upper()
        if not address:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="missing_button_address",
            )

        coordinator = _loaded_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_loaded_entry",
            )
        await coordinator.async_event_handler("ha_button_pressed", {"address": address})

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_BUTTON_PRESS,
        handle_send_button_press,
        SEND_BUTTON_PRESS_SCHEMA,
    )
    return True


def _loaded_coordinator(hass: HomeAssistant) -> NikobusDataCoordinator | None:
    """Return the coordinator of the first loaded Nikobus entry, or None."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


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

    # 3. Forward setup to platforms FIRST
    # This allows entities to be created and register their dispatcher listeners.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 4. Reload when the user changes options via the OptionsFlow
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # 5. Trigger the first refresh
    # Since platforms are loaded, entities will correctly receive the "Targeted update signal".
    _LOGGER.debug("Performing initial Nikobus data synchronization")
    await coordinator.async_config_entry_first_refresh()

    # 6. Clean up stale entities
    await _async_cleanup_orphan_entities(hass, entry, coordinator)

    # 7. One-shot: migrate legacy per-button descriptions to device name_by_user.
    await _async_migrate_legacy_button_names(hass)

    # 8. Surface repair issues for actionable misconfigurations.
    coordinator.refresh_repair_issues()

    _LOGGER.info("Nikobus integration setup complete")
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

    # Remove orphan devices (except the hub). A device is kept when it either
    # has at least one entity of its own OR acts as the ``via_device`` parent
    # of another device in this entry (e.g. a physical wall button that groups
    # its soft-button children but has no entity of its own).
    hub_identifier = (DOMAIN, HUB_IDENTIFIER)
    devices_with_entities = {
        entity.device_id for entity in ent_reg.entities.values()
        if entity.config_entry_id == entry.entry_id and entity.device_id
    }
    via_parent_ids = {
        device.via_device_id for device in dev_reg.devices.values()
        if device.via_device_id and entry.entry_id in device.config_entries
    }

    for device in list(dev_reg.devices.values()):
        if entry.entry_id in device.config_entries and hub_identifier not in device.identifiers:
            if device.id in devices_with_entities or device.id in via_parent_ids:
                continue
            dev_reg.async_remove_device(device.id)

_LEGACY_BUTTON_CONFIG_FILE: Final = "nikobus_button_config.json"


async def _async_migrate_legacy_button_names(hass: HomeAssistant) -> None:
    """One-shot: copy per-button ``description`` from the legacy JSON to HA.

    Versions prior to the v2 schema persisted user-edited names in
    ``config/nikobus_button_config.json``, keyed by bus address. That file is
    no longer read or written by the integration (button data now lives in
    ``.storage/nikobus.buttons``), so any custom names the user typed in the
    old file are invisible.

    This function loads the legacy file when present, walks its entries, and
    applies each entry's ``description`` to the matching HA device's
    ``name_by_user`` — only when the user has not already renamed the device
    via the UI. Wall-button parent devices are new to the v2 schema and do
    not appear in the legacy file, so they are never touched.

    On success the file is renamed to ``.migrated`` so the migration runs
    exactly once per installation. Errors are logged and swallowed; the
    integration continues to start normally even if the migration fails.
    """
    file_path = hass.config.path(_LEGACY_BUTTON_CONFIG_FILE)
    if not await hass.async_add_executor_job(os.path.isfile, file_path):
        return

    try:
        async with aio_open(file_path, mode="r") as fh:
            raw = await fh.read()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning(
            "Legacy button config at %s is unreadable — skipping name migration: %s",
            file_path,
            err,
        )
        return

    entries = data.get("nikobus_button", []) if isinstance(data, dict) else []
    if not isinstance(entries, list) or not entries:
        _LOGGER.debug("Legacy button config has no entries to migrate")
        return

    # Build {bus_address_upper: description}, skipping auto-generated placeholders.
    name_map: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        addr = str(item.get("address") or "").upper()
        desc = item.get("description")
        if not addr or not isinstance(desc, str) or not desc.strip():
            continue
        if desc.startswith("DISCOVERED"):
            continue
        name_map[addr] = desc

    if not name_map:
        _LOGGER.debug("Legacy button config has no user-written names to migrate")
        return

    dev_reg = dr.async_get(hass)
    applied = 0
    skipped_existing = 0
    unmatched: list[str] = []

    for addr, desc in name_map.items():
        device = dev_reg.async_get_device(identifiers={(DOMAIN, addr)})
        if device is None:
            unmatched.append(addr)
            continue
        if device.name_by_user:
            skipped_existing += 1
            continue
        dev_reg.async_update_device(device.id, name_by_user=desc)
        applied += 1

    migrated_path = file_path + ".migrated"
    try:
        await hass.async_add_executor_job(os.replace, file_path, migrated_path)
    except OSError as err:
        _LOGGER.warning(
            "Could not rename %s to %s after migration: %s",
            file_path,
            migrated_path,
            err,
        )

    _LOGGER.info(
        "Migrated %d button name(s) from legacy config; skipped %d already-renamed, "
        "%d address(es) had no matching device. File renamed to %s.",
        applied,
        skipped_existing,
        len(unmatched),
        migrated_path,
    )
    if unmatched:
        _LOGGER.debug("Unmatched legacy addresses: %s", ", ".join(sorted(unmatched)))


async def async_unload_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Unload the integration and stop background tasks."""
    coordinator = entry.runtime_data
    if coordinator:
        await coordinator.stop()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)