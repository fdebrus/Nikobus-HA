"""The Nikobus integration."""
from __future__ import annotations

import logging
from typing import Any, Final

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
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

from .const import CONFIG_ENTRY_VERSION, DOMAIN, HUB_IDENTIFIER
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import hub_device_info
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
SERVICE_DETECT_STALE_INVENTORY: Final = "detect_stale_inventory"
SERVICE_PURGE_STALE_INVENTORY: Final = "purge_stale_inventory"

# Default per-module probe budget. The library's
# ``NikobusDiscovery.detect_stale_inventory`` polls each candidate module
# with a $1012/$1017 read and waits this long for an ACK before flagging
# the module absent. Worst-case wall-clock budget is roughly
# ``len(switch+dimmer+roller modules) * timeout`` — at the default 0.6 s
# an 8-module install needs about five seconds.
DEFAULT_DETECT_OUTER_ATTEMPTS: Final[int] = 1
DEFAULT_DETECT_OUTER_DELAY: Final[float] = 0.0

def _parse_register_byte(value: Any) -> int:
    """Accept an int 0..255 or a hex string (``"FF"``, ``"0xFF"``)."""
    if isinstance(value, bool):
        raise vol.Invalid("Register must be an integer or hex string, not a bool")
    if isinstance(value, int):
        n = value
    else:
        text = str(value).strip()
        if not text:
            raise vol.Invalid("Register cannot be empty")
        if text.lower().startswith("0x"):
            text = text[2:]
        try:
            n = int(text, 16)
        except ValueError:
            raise vol.Invalid(f"Cannot parse '{value}' as a hex register byte") from None
    if not (0 <= n <= 0xFF):
        raise vol.Invalid(f"Register {n:#x} out of range 0x00..0xFF")
    return n


def _parse_sub_byte(value: Any) -> str:
    """Accept a 2-char hex string and return the canonical uppercase form."""
    text = str(value or "").strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if len(text) not in (1, 2) or not all(c in "0123456789abcdefABCDEF" for c in text):
        raise vol.Invalid(f"sub_byte must be a hex byte (e.g. '04'), got: {value!r}")
    return text.upper().zfill(2)


SCAN_MODULE_SCHEMA = vol.Schema({
    vol.Optional("module_address", default=""): cv.string,
    # Forensic-mode trio: when register_start AND register_end are both
    # provided, the scan walks only that range with the given sub_byte
    # (default "04") and skips the library's extra-pass / non-output-
    # module guard logic. Used for reverse-engineering storage layouts
    # of modules the production scan declines or doesn't fully cover.
    # Requires a specific module_address — not compatible with ALL mode.
    vol.Optional("register_start"): _parse_register_byte,
    vol.Optional("register_end"): _parse_register_byte,
    vol.Optional("sub_byte"): _parse_sub_byte,
})
SEND_BUTTON_PRESS_SCHEMA = vol.Schema({
    vol.Required("address"): cv.string,
})
DETECT_STALE_INVENTORY_SCHEMA = vol.Schema({
    # nikobus-connect 0.5.21 removed the per-probe ``timeout`` kwarg in
    # favour of letting each probe run its natural ~15 s budget (3 inner
    # attempts × 5 s). Tunable knobs are now the outer-loop pair:
    #  * ``outer_attempts`` — how many full sweep passes (default 1)
    #  * ``outer_delay``    — bus-quiet wait between passes (default 0)
    # Slow installs (IKIKN-class) opt into 2-pass with a 3 s gap.
    vol.Optional("outer_attempts", default=DEFAULT_DETECT_OUTER_ATTEMPTS): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=3)
    ),
    vol.Optional("outer_delay", default=DEFAULT_DETECT_OUTER_DELAY): vol.All(
        vol.Coerce(float), vol.Range(min=0.0, max=10.0)
    ),
})
PURGE_STALE_INVENTORY_SCHEMA = vol.Schema({
    vol.Required("addresses"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
})

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services."""

    async def handle_module_discovery(call: ServiceCall) -> None:
        """Trigger a Nikobus inventory scan via the coordinator's discovery engine."""
        module_address = (call.data.get("module_address", "") or "").strip().upper()
        register_start = call.data.get("register_start")
        register_end = call.data.get("register_end")
        sub_byte = call.data.get("sub_byte")

        # Forensic mode validation surfaces here so the user gets a
        # ServiceValidationError instead of the library's bare
        # ValueError, with a more actionable translation message.
        custom_range = register_start is not None or register_end is not None
        if custom_range:
            if not module_address:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="forensic_requires_module_address",
                )
            if register_start is None or register_end is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="forensic_range_incomplete",
                )

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
            await coordinator.nikobus_discovery.start_inventory_discovery()
        elif custom_range:
            _LOGGER.info(
                "Forensic scan of module %s — registers 0x%02X..0x%02X, sub=%s",
                module_address,
                register_start,
                register_end,
                sub_byte or "04",
            )
            await coordinator.nikobus_discovery.query_module_inventory(
                module_address,
                register_start=register_start,
                register_end=register_end,
                sub_byte=sub_byte,
            )
        else:
            _LOGGER.info("Starting discovery for module %s", module_address)
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

    async def handle_detect_stale_inventory(call: ServiceCall) -> ServiceResponse:
        """Probe known output modules for liveness; return the manifest.

        Wraps ``NikobusDiscovery.detect_stale_inventory`` (nikobus-connect
        0.5.16+). The library deliberately doesn't mutate storage —
        ``purge_stale_inventory`` consumes this manifest after the user
        confirms which addresses to remove.
        """
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

        outer_attempts = int(call.data.get("outer_attempts", DEFAULT_DETECT_OUTER_ATTEMPTS))
        outer_delay = float(call.data.get("outer_delay", DEFAULT_DETECT_OUTER_DELAY))
        _LOGGER.info(
            "Detecting stale Nikobus inventory (outer_attempts=%d outer_delay=%.1fs)",
            outer_attempts,
            outer_delay,
        )
        manifest: dict[str, Any] = await coordinator.nikobus_discovery.detect_stale_inventory(
            outer_attempts=outer_attempts,
            outer_delay=outer_delay,
        )
        absent = manifest.get("absent_modules") or []
        orphaned = manifest.get("orphaned_buttons") or []
        _LOGGER.info(
            "Stale-inventory probe done: checked=%d present=%d absent=%d orphaned_buttons=%d",
            len(manifest.get("checked") or []),
            len(manifest.get("present_modules") or []),
            len(absent),
            len(orphaned),
        )
        return manifest

    hass.services.async_register(
        DOMAIN,
        SERVICE_DETECT_STALE_INVENTORY,
        handle_detect_stale_inventory,
        DETECT_STALE_INVENTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def handle_purge_stale_inventory(call: ServiceCall) -> ServiceResponse:
        """Remove the given addresses from the persisted module + button stores.

        Delegates to ``coordinator.purge_inventory_addresses`` so the
        storage write path lives next to the storage objects themselves.
        Trust-the-input: the user (or a UI flow built on top of
        ``detect_stale_inventory``) supplies the address list.
        """
        coordinator = _loaded_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_loaded_entry",
            )

        result = await coordinator.purge_inventory_addresses(
            call.data.get("addresses") or []
        )
        _LOGGER.info(
            "Purged stale Nikobus inventory: modules=%s buttons=%s not_found=%s",
            result["removed_modules"],
            result["removed_buttons"],
            result["not_found"],
        )
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_PURGE_STALE_INVENTORY,
        handle_purge_stale_inventory,
        PURGE_STALE_INVENTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


def _loaded_coordinator(hass: HomeAssistant) -> NikobusDataCoordinator | None:
    """Return the coordinator of the first loaded Nikobus entry, or None."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


async def async_migrate_entry(
    hass: HomeAssistant, entry: NikobusConfigEntry
) -> bool:
    """Migrate a config entry to the current schema version.

    The schema is at version 1 since the first release, so there is no
    transformation to perform yet — this handler exists so HA refuses to
    load entries from a *future* major version (downgrade protection)
    and so the next schema change only has to add its migration step
    here instead of wiring the whole mechanism.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        # Entry was created by a newer release of this integration —
        # downgrading data is unsupported, refuse to load it.
        _LOGGER.error(
            "Cannot migrate Nikobus config entry from version %s.%s "
            "(created by a newer release than installed %s)",
            entry.version,
            entry.minor_version,
            CONFIG_ENTRY_VERSION,
        )
        return False
    # Future migrations go here, e.g.:
    # if entry.version == 1:
    #     hass.config_entries.async_update_entry(entry, data={...}, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Set up the Nikobus integration (single-instance) without redundant handshakes."""
    _LOGGER.debug("Starting setup of Nikobus (single-instance)")

    # 1. Initialize the coordinator
    # We create the object but ensure it doesn't broadcast data prematurely.
    coordinator = NikobusDataCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # 2. Connect and prepare internal buffers.
    # ``connect`` opens the transport before initialising the listener /
    # command stack, so a failure mid-setup can leave the bus open. Tear
    # the coordinator back down before retrying — only one client may hold
    # the bus, so a leaked connection would make every retry fail.
    try:
        await coordinator.connect()
    except NikobusConnectionError as err:
        await coordinator.stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="communication_error",
            translation_placeholders={"error": str(err)},
        ) from err
    except NikobusDataError as err:
        await coordinator.stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="setup_data_error",
            translation_placeholders={"error": str(err)},
        ) from err
    except NikobusError as err:
        await coordinator.stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="initialization_error",
            translation_placeholders={"error": str(err)},
        ) from err

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

    # 7. Surface repair issues for actionable misconfigurations.
    coordinator.refresh_repair_issues()

    _LOGGER.info("Nikobus integration setup complete")
    return True


async def _async_options_updated(hass: HomeAssistant, entry: NikobusConfigEntry) -> None:
    """Reload the integration when the user changes options.

    Scheduled as a background task rather than awaited, because HA awaits
    update listeners before closing the options flow and responding to
    the frontend. A reload triggered by the discovery options flow is
    slow (unload + re-forward setup to every platform, rebuilding
    entities for every freshly-discovered module/button); awaiting it
    here causes the flow-close HTTP request to time out, which the UI
    renders as a generic "Invalid flow specified" error. Firing the
    reload as a task lets the flow finalize immediately; entities
    refresh when the reload completes in the background.
    """
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


def _register_hub_device(hass: HomeAssistant, entry: NikobusConfigEntry) -> None:
    """Register the Nikobus bridge (hub) as a device."""
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info()
    )
    _register_category_devices(hass, entry)


def _register_category_devices(
    hass: HomeAssistant, entry: NikobusConfigEntry
) -> None:
    """Register intermediate category devices for hierarchical UI grouping.

    Each category device sits between the hub and the real devices,
    parenting same-class devices into a collapsible group in HA's
    device list. Categories with no real children get cleaned up by
    ``_async_cleanup_orphan_entities`` (kept-when-has-children rule),
    so empty categories don't clutter the UI.
    """
    from .const import CATEGORY_DEVICES  # local import to avoid cycle

    device_registry = dr.async_get(hass)
    for category_id, display_name, model in CATEGORY_DEVICES:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, category_id)},
            manufacturer="Niko",
            name=display_name,
            model=model,
            via_device=(DOMAIN, HUB_IDENTIFIER),
            entry_type=dr.DeviceEntryType.SERVICE,
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


async def async_unload_entry(hass: HomeAssistant, entry: NikobusConfigEntry) -> bool:
    """Unload the integration and stop background tasks.

    Unload the platforms first so entities are gone before the protocol
    stack is torn down, and only stop the coordinator if that succeeded —
    a refused unload must not leave a stopped coordinator behind a still
    loaded entry.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and (coordinator := entry.runtime_data):
        await coordinator.stop()
    return unload_ok