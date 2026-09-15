"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_NKB_IMPORT_CATEGORIES,
    CONF_NKB_IMPORT_OVERWRITE,
    DOMAIN,
    NKB_IMPORT_CATEGORIES,
    SIGNAL_DISCOVERY_STATE,
)
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .devices import (  # noqa: F401 - re-exported for the other platforms
    _category_for_button_type,
    op_point_display_name,
    register_input_module_devices,
    register_opaque_module_devices,
    register_wall_button_devices,
)
from .entity import NikobusEntity, hub_device_info
from .router import calendar_channel_naming, iter_operation_points

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus button entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data

    entities: list[ButtonEntity] = [
        NikobusPcLinkInventoryButton(coordinator),
        NikobusModuleScanButton(coordinator),
        NikobusImportNkbNamesButton(coordinator),
        NikobusSyncClockButton(coordinator),
        NikobusVerifyProgrammingButton(coordinator),
        NikobusBackupProgrammingButton(coordinator),
    ]

    buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
    register_wall_button_devices(hass, entry, buttons, coordinator.dict_module_data)
    entities.extend(_iter_button_entities(coordinator, buttons))

    # Input-class modules (PC-Logic, Modular Interface) — register one
    # device per module address. Their inputs are surfaced as synthesized
    # LM-INPUT / MI-INPUT button entries (register_wall_button_devices), so
    # there are no per-channel entities to add here.
    register_input_module_devices(hass, entry, coordinator.dict_module_data)

    # Opaque modules (Audio Distribution) — register the device so it's
    # visible in HA's device registry, but don't create any entities
    # for it yet (input/output schema not validated).
    register_opaque_module_devices(hass, entry, coordinator.dict_module_data)

    async_add_entities(entities)


def _iter_button_entities(
    coordinator: NikobusDataCoordinator,
    buttons: dict[str, Any],
) -> Iterator[NikobusButtonEntity]:
    """Yield one NikobusButtonEntity per discovered operation point."""
    for physical_addr, key_label, op_point, phys in iter_operation_points(buttons):
        if calendar_channel_naming(phys) is not None:
            # A PC-Link calendar channel is fired by the PC-Link's own
            # calendar programs; what it puts on the bus is not known,
            # so there is nothing Home Assistant could press.
            continue
        yield NikobusButtonEntity(
            coordinator, physical_addr, key_label, op_point, parent_phys=phys
        )


class _NikobusBridgeButton(ButtonEntity):
    """Base for the bridge action buttons.

    Shared: hub device placement, CONFIG category, and availability
    gating — every bridge button greys out while a discovery scan is
    running (3.11.0 field report: the buttons stayed enabled with no
    visual feedback, inviting double-triggers). ``SIGNAL_DISCOVERY_STATE``
    fires on every discovery state change, including the
    ``discovery_running`` flips, so availability repaints live.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_device_info = hub_device_info()

    @property
    def available(self) -> bool:
        # One bridge action at a time: discovery and the programming
        # maintenance runs share the bus, so every bridge button greys
        # out while any of them is busy.
        return not (self._coordinator.discovery_running or self._maintenance_running)

    @property
    def _maintenance_running(self) -> bool:
        programming = getattr(self._coordinator, "programming", None)
        return getattr(programming, "running", False) is True

    async def async_added_to_hass(self) -> None:
        """Re-render availability whenever discovery state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DISCOVERY_STATE, self._handle_discovery_update
            )
        )

    @callback
    def _handle_discovery_update(self) -> None:
        self.async_write_ha_state()


class NikobusPcLinkInventoryButton(_NikobusBridgeButton):
    """Bridge button that starts a PC Link inventory discovery."""

    _attr_translation_key = "discover_modules_buttons"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_pc_link_inventory_button"

    async def async_press(self) -> None:
        """Start PC Link inventory discovery.

        Scheduled as a background task — the inventory probe + register
        scan can take 30-60 s, which is too long to hold a UI button
        handler open. If the press happens during HA startup, blocking
        here also stalls bootstrap stage 2 (see GH discussion in 2.12).
        Progress is surfaced via ``SIGNAL_DISCOVERY_STATE``; failures
        flow through the same channel as the discovery state's error
        field, plus the integration log.
        """
        _LOGGER.info("PC-Link inventory discovery triggered via UI button")
        if self._coordinator.discovery_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="discovery_already_running",
            )
        self.hass.async_create_background_task(
            self._coordinator.start_pc_link_inventory(),
            name="nikobus_pc_link_inventory_discovery",
        )


class NikobusModuleScanButton(_NikobusBridgeButton):
    """Bridge button that starts a full module scan for button links.

    Greyed out in the UI until at least one output-capable module is
    known — the scan walks the list of known modules, so it has nothing
    to do before a PC Link inventory (or legacy-file migration) has
    populated storage. Also greyed (via the base class) while any
    discovery scan is running.
    """

    _attr_translation_key = "scan_all_module_links"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_module_scan_button"

    @property
    def available(self) -> bool:
        # The base class greys the button while a discovery or a
        # programming maintenance run (verify / backup / clock sync)
        # owns the bus; this button adds the "something to scan" gate.
        return self._coordinator.has_known_output_modules and super().available

    async def async_press(self) -> None:
        """Scan all output modules for button links.

        Backgrounded for the same reason as the PC-Link inventory
        button — a full register scan can take 2+ minutes on big
        installs, and awaiting it here would block the button handler
        (and HA bootstrap stage 2 if pressed during startup).
        """
        _LOGGER.info("Module scan discovery triggered via UI button")
        if self._coordinator.discovery_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="discovery_already_running",
            )
        if self._maintenance_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="maintenance_running",
            )
        self.hass.async_create_background_task(
            self._coordinator.start_module_scan(),
            name="nikobus_module_scan_discovery",
        )


class NikobusImportNkbNamesButton(_NikobusBridgeButton):
    """Bridge button that imports device/entity names from a ``.nkb`` file.

    Reads the Nikobus PC-software project export (a ``.nkb`` placed in the
    HA config dir) and applies its module / button / IR-receiver names as
    suggested device and entity names. Replays the last-used settings
    from the options flow's "Import from .nkb" step; before that step
    has ever run it imports everything, non-destructively (manual
    renames preserved).
    """

    _attr_translation_key = "import_nkb_names"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_import_nkb_names_button"

    async def async_press(self) -> None:
        """Import names from the ``.nkb`` in the config dir.

        Awaited directly (not backgrounded): the parse runs in an
        executor and the registry writes are fast, so this returns
        quickly while still surfacing not-found / parse errors to the
        user as the button action result.
        """
        # Replay the last-used import settings (persisted by the options
        # flow's "Import from .nkb" step) so the two import paths behave
        # identically. First-ever use: everything, non-destructive.
        options = self._coordinator.config_entry.options
        stored_cats = options.get(CONF_NKB_IMPORT_CATEGORIES)
        categories = (
            {c for c in NKB_IMPORT_CATEGORIES if c in stored_cats}
            if isinstance(stored_cats, (list, tuple, set)) and stored_cats
            else None
        )
        overwrite = bool(options.get(CONF_NKB_IMPORT_OVERWRITE, False))
        _LOGGER.info(
            "Importing Nikobus names from .nkb via UI button "
            "(categories=%s, overwrite=%s)",
            sorted(categories) if categories else "all",
            overwrite,
        )
        result = await self._coordinator.async_import_nkb_names(
            categories=categories, overwrite=overwrite
        )
        _LOGGER.info(
            "Nikobus .nkb import done: %s devices, %s entities, %s channels, "
            "%s outputs enabled, %s areas, %s scenes named",
            result["devices"],
            result["entities"],
            result.get("channels", 0),
            result.get("outputs_enabled", 0),
            result["areas"],
            result["scenes"],
        )


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Representation of a Nikobus operation-point (software trigger).

    One entity per ``(physical_address, key_label)`` pair; grouped under the
    physical-button device in the registry.

    Categorised DIAGNOSTIC (3.13.0): a press-simulation is a tool, not a
    day-to-day control — the lights/covers/switches are. On a real
    install these ~150 entities drowned out the ~50 actual outputs on
    device pages and auto-generated dashboards. Diagnostic entities
    stay fully usable (device page, automations, scripts); they're just
    no longer auto-placed as controls.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        physical_address: str,
        key_label: str,
        op_point: dict[str, Any],
        *,
        parent_phys: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the button entity."""
        bus_addr = op_point["bus_address"]
        self._physical_address = physical_address
        self._key_label = key_label

        name = op_point_display_name(
            physical_address, key_label, op_point, parent_phys=parent_phys
        )

        # PC-Logic logical-input keys appear as "PC-Logic Key" model
        # so the device-info popover distinguishes them from physical
        # wall-button keys at a glance.
        is_pc_logic_input = (
            isinstance(parent_phys, dict)
            and parent_phys.get("pc_logic_parent_address") is not None
        )
        model = "PC-Logic Key" if is_pc_logic_input else "Push Button"

        super().__init__(
            coordinator=coordinator,
            address=bus_addr,
            name=name,
            model=model,
            via_device=(DOMAIN, physical_address),
        )

        self._attr_unique_id = f"{DOMAIN}_push_button_{bus_addr}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose physical-button parent info and linked module outputs."""
        parent_attrs = super().extra_state_attributes or {}
        attrs: dict[str, Any] = {
            **parent_attrs,
            "linked_outputs": self.coordinator.get_button_linked_outputs(self._address),
            "wall_button_address": self._physical_address,
            "wall_button_key": self._key_label,
        }
        wall_info = self.coordinator.get_wall_button_info(self._address)
        if wall_info:
            attrs["wall_button_model"] = wall_info.get("model")
            attrs["wall_button_type"] = wall_info.get("type")
            # ``status`` comes from post-discovery reconciliation
            # (coordinator._reconcile_post_discovery): one of "active",
            # "legacy_orphan", "legacy_undecoded", "synthesized_input",
            # "input_only". Surface only the legacy flags so healthy
            # buttons (active wall buttons, synthesized PC-Logic /
            # 05-206 inputs, and input-only Universal Interfaces) don't
            # get cluttered.
            status = wall_info.get("status")
            if status in ("legacy_orphan", "legacy_undecoded"):
                attrs["wall_button_status"] = status
        # Cross-reference: if this button's address is also a discovered
        # CF/light scene, surface the scene it fires.
        scene = self.coordinator.get_scene_for_address(self._address)
        if scene:
            members = len(scene.get("outputs") or [])
            attrs["triggers_scene"] = f"Nikobus scene {self._address} ({members} ch)"
        return attrs

    async def async_press(self) -> None:
        """Execute the button press command on the Nikobus bus."""
        _LOGGER.debug("UI button pressed for address %s", self._address)
        await self.coordinator.async_event_handler(
            "ha_button_pressed", {"address": self._address}
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Stateless entity — ignore coordinator state updates."""

class _NikobusMaintenanceButton(_NikobusBridgeButton):
    """Bridge buttons that read the modules' programming.

    Availability follows the bridge rule (one action at a time); the
    press handlers re-check it so a race can't start two bus runs.
    """

    def _start(self, coro: Any, name: str) -> None:
        programming = self._coordinator.programming
        if self._coordinator.discovery_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="discovery_already_running"
            )
        if programming.running:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="maintenance_running"
            )

        async def _run() -> None:
            try:
                await coro
            except Exception:  # a failed run is reported, not lost
                _LOGGER.exception("%s failed", name)

        self.hass.async_create_background_task(_run(), name=name)


class NikobusSyncClockButton(_NikobusMaintenanceButton):
    """Set the PC-Link clock from Home Assistant's time."""

    _attr_translation_key = "sync_pc_link_clock"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_sync_pc_link_clock_button"

    async def async_press(self) -> None:
        await self._coordinator.programming.async_sync_clock()


class NikobusVerifyProgrammingButton(_NikobusMaintenanceButton):
    """Check every output module's status and memory CRC."""

    _attr_translation_key = "verify_module_programming"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_verify_programming_button"

    async def async_press(self) -> None:
        _LOGGER.info("Module programming check triggered via UI button")
        self._start(
            self._coordinator.programming.async_verify_modules(),
            "nikobus_verify_programming",
        )


class NikobusBackupProgrammingButton(_NikobusMaintenanceButton):
    """Read every output module's programming image into a backup folder."""

    _attr_translation_key = "backup_module_programming"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_backup_programming_button"

    async def async_press(self) -> None:
        _LOGGER.info("Module programming backup triggered via UI button")
        self._start(
            self._coordinator.programming.async_backup_modules(),
            "nikobus_backup_programming",
        )

