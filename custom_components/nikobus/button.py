"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    BRAND,
    CATEGORY_INTERFACES,
    CATEGORY_REMOTES,
    CATEGORY_SYSTEM_MODULES,
    CATEGORY_WALL_BUTTONS,
    DOMAIN,
    SIGNAL_DISCOVERY_STATE,
)
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity, hub_device_info
from .router import (
    INPUT_MODULE_TYPES,
    OPAQUE_MODULE_TYPES,
    input_label_prefix,
    iter_operation_points,
    pc_logic_input_naming,
)

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
        yield NikobusButtonEntity(
            coordinator, physical_addr, key_label, op_point, parent_phys=phys
        )


def _category_for_button_type(type_str: str) -> str:
    """Return the category device identifier appropriate for a button's type.

    Classification rule based on the discovery-supplied ``type`` field:

      * ``Interface`` anywhere → Interfaces (push-button / switch /
        universal input interfaces — non-keypad input sources)
      * ``RF`` anywhere → Remotes (RF hand-held / RF wall transmitters)
      * everything else → Wall buttons (physical bus push buttons)

    Interface is matched before RF because ``"rf"`` is a substring of
    ``"interface"`` — checking RF first would route every Universal /
    Modular / push-button interface into Remotes.
    """
    lowered = type_str.lower()
    if "interface" in lowered:
        return CATEGORY_INTERFACES
    if "rf" in lowered:
        return CATEGORY_REMOTES
    return CATEGORY_WALL_BUTTONS


def _remote_transmitter_naming(
    phys: dict[str, Any],
) -> tuple[str, tuple[str, str]] | None:
    """Return ``(device_name, via_device_identifier)`` if ``phys`` is a
    synthesised remote-transmitter child entry; else ``None``.

    The library's cluster-detection pass synthesises a virtual
    transmitter parent for any cluster of 8+ unmatched bus addresses
    sharing a 4-hex suffix (typical for multi-page Easywave remotes
    emitting dozens of distinct codes). Each child carries
    ``remote_transmitter_address`` (the synthetic parent ID, e.g.
    ``RT-E31C``) and ``remote_transmitter_bus_address`` (the original
    observed bus event). HA renders each child as a
    ``Remote <bus>`` device parented under the transmitter.
    """

    parent_id = phys.get("remote_transmitter_address")
    bus_addr = phys.get("remote_transmitter_bus_address")
    if not isinstance(parent_id, str) or not isinstance(bus_addr, str):
        return None
    return f"Remote {bus_addr}", (DOMAIN, parent_id)


def register_wall_button_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    buttons: dict[str, Any],
    dict_module_data: dict[str, Any] | None = None,
) -> None:
    """Register one device per physical wall button (top-level address).

    Groups the N operation-points of a keypad/IR remote under a single parent
    device in the device registry. The default name is taken straight from
    the discovery metadata (``{type} ({address})``) so it is identical for
    every installation; HA preserves any user rename via ``name_by_user``
    across reloads. Idempotent: safe to call from multiple platforms.

    The button's ``via_device`` parent is one of the category devices —
    Wall buttons / Remotes / Interfaces — chosen by
    ``_category_for_button_type`` so the integration's device list
    nests by class rather than dumping everything under the bridge.

    Synthesized input-module children (the library's
    ``_synthesize_pc_logic_inputs`` adds these with ``pc_logic_*``
    provenance fields) are routed differently: the device is parented
    directly under the owning module, and the name follows the Niko
    ``LM-INPUT N`` (PC-Logic) / ``MI-INPUT N`` (Modular Interface)
    convention.
    """
    device_registry = dr.async_get(hass)
    pc_logic_parents_registered: set[str] = set()
    remote_transmitter_parents_registered: set[str] = set()
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue

        pc_logic_naming = pc_logic_input_naming(phys)
        if pc_logic_naming is not None:
            name, via_device = pc_logic_naming
            parent_addr = via_device[1]
            # HA 2025.12 enforces via_device referencing an existing device.
            # The PC-Logic module is normally registered by
            # register_input_module_devices, but that runs from a different
            # call site (and possibly after this one) — pre-register the
            # parent here so the child's via_device always resolves.
            if parent_addr not in pc_logic_parents_registered:
                _ensure_pc_logic_parent_device(
                    device_registry, entry, parent_addr, dict_module_data
                )
                pc_logic_parents_registered.add(parent_addr)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, physical_addr)},
                manufacturer=BRAND,
                name=name,
                model=str(phys.get("model") or "PC-Logic Logical Input"),
                via_device=via_device,
            )
            continue

        remote_naming = _remote_transmitter_naming(phys)
        if remote_naming is not None:
            name, via_device = remote_naming
            parent_id = via_device[1]
            if parent_id not in remote_transmitter_parents_registered:
                _ensure_remote_transmitter_parent_device(
                    device_registry, entry, parent_id, phys
                )
                remote_transmitter_parents_registered.add(parent_id)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, physical_addr)},
                manufacturer=BRAND,
                name=name,
                model=str(phys.get("model") or "Remote Code"),
                via_device=via_device,
            )
            continue

        type_str = str(phys.get("type") or phys.get("model") or "Wall Button")
        model = str(phys.get("model") or phys.get("type") or "Wall Button")
        category = _category_for_button_type(type_str)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, physical_addr)},
            manufacturer=BRAND,
            name=f"{type_str} ({physical_addr})",
            model=model,
            via_device=(DOMAIN, category),
        )


def _ensure_remote_transmitter_parent_device(
    device_registry: dr.DeviceRegistry,
    entry: NikobusConfigEntry,
    transmitter_id: str,
    sample_child: dict[str, Any],
) -> None:
    """Register a synthetic remote-transmitter parent device.

    Unlike PC-Logic / interface_module parents (which are real
    Nikobus modules with an enrolled bus address and a record in
    ``dict_module_data``), the transmitter parent is a purely
    HA-side construct synthesised from a cluster of unmatched bus
    references. The identifier is ``(DOMAIN, "RT-<suffix>")`` and
    the device is parented under the Remotes category so it
    appears grouped with other RF transmitters in the HA device
    list.
    """

    suffix = sample_child.get("remote_transmitter_suffix") or transmitter_id
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, transmitter_id)},
        manufacturer=BRAND,
        name=f"Remote Transmitter ({suffix})",
        model="RF Remote (synthesized)",
        via_device=(DOMAIN, CATEGORY_REMOTES),
    )


def _ensure_pc_logic_parent_device(
    device_registry: dr.DeviceRegistry,
    entry: NikobusConfigEntry,
    parent_addr: str,
    dict_module_data: dict[str, Any] | None,
) -> None:
    """Register the input-module device that a synthesised input-child
    points to via ``via_device``.

    Looks the parent module up in ``dict_module_data`` under both the
    ``pc_logic`` and ``interface_module`` buckets — both module types
    use the same synthesis path and provenance shape, so the parent
    can live in either bucket. Falls back to a placeholder when module
    data isn't available — a later call to ``register_input_module_devices``
    will update fields on the same identifier.
    """
    module_data: dict[str, Any] | None = None
    found_module_type: str | None = None
    for module_type in ("pc_logic", "interface_module"):
        bucket = (dict_module_data or {}).get(module_type) or {}
        if not isinstance(bucket, dict):
            continue
        for addr, data in bucket.items():
            if str(addr).upper() == parent_addr and isinstance(data, dict):
                module_data = data
                found_module_type = module_type
                break
        if module_data is not None:
            break

    if module_data is not None:
        default_name = (
            f"PC-Logic ({parent_addr})"
            if found_module_type == "pc_logic"
            else f"Modular Interface ({parent_addr})"
        )
        name = str(module_data.get("description") or default_name)
        model = str(module_data.get("model") or found_module_type)
    else:
        name = f"Input Module ({parent_addr})"
        model = "input_module"

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, parent_addr)},
        manufacturer=BRAND,
        name=name,
        model=model,
        via_device=(DOMAIN, CATEGORY_SYSTEM_MODULES),
    )


def _iter_module_records(
    dict_module_data: dict[str, Any], module_types: frozenset[str]
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(module_type, address, module_data)`` for the requested buckets."""
    for module_type in module_types:
        bucket = dict_module_data.get(module_type)
        if not isinstance(bucket, dict):
            continue
        for address, module_data in bucket.items():
            if isinstance(module_data, dict):
                yield module_type, str(address).upper(), module_data


def register_input_module_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    dict_module_data: dict[str, Any],
) -> None:
    """Register one device per PC-Logic / Modular Interface module.

    Each input module groups its N input channels (LM01–LM06 on PC-Logic,
    six inputs on the Modular Interface) under a single parent device in
    the registry, mirroring the wall-button device layout.
    """
    device_registry = dr.async_get(hass)
    for module_type, address, module_data in _iter_module_records(
        dict_module_data, INPUT_MODULE_TYPES
    ):
        description = str(
            module_data.get("description") or f"{module_type} ({address})"
        )
        model = str(module_data.get("model") or module_type)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, address)},
            manufacturer=BRAND,
            name=description,
            model=model,
            via_device=(DOMAIN, CATEGORY_SYSTEM_MODULES),
        )


def register_opaque_module_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    dict_module_data: dict[str, Any],
) -> None:
    """Register a placeholder device per Audio Distribution module.

    Audio modules surface no entities yet (input/output schema not
    validated), but registering the device keeps them visible in the HA
    device registry so users can confirm discovery saw them.
    """
    device_registry = dr.async_get(hass)
    for module_type, address, module_data in _iter_module_records(
        dict_module_data, OPAQUE_MODULE_TYPES
    ):
        description = str(
            module_data.get("description") or f"{module_type} ({address})"
        )
        model = str(module_data.get("model") or module_type)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, address)},
            manufacturer=BRAND,
            name=description,
            model=model,
            via_device=(DOMAIN, CATEGORY_SYSTEM_MODULES),
        )


def op_point_display_name(
    physical_address: str,
    key_label: str,
    op_point: dict[str, Any],
    *,
    parent_phys: dict[str, Any] | None = None,
) -> str:
    """Build a UI-visible name for an op-point's device entry.

    IR op-points (storage keys starting with ``IR:``) get the receiver's
    bus address appended so the same IR code registered on different
    receivers remains distinguishable in the device list — the
    library-generated description ("IR code 30A #I30A") is identical for
    every receiver that learned the same code. Wall keys keep the
    library description verbatim; it already carries the channel label.

    Input-module keys (the parent button carries
    ``pc_logic_parent_address``) render as ``Key A on LM-INPUT N`` /
    ``Key B on LM-INPUT N`` for PC-Logic, or ``... on MI-INPUT N`` for
    the Modular Interface, mirroring the IR ``<key> on <parent>``
    pattern so the device list disambiguates each slot's keys.
    """
    if key_label.startswith("IR:"):
        ir_code = key_label[len("IR:"):]
        return f"IR {ir_code} on {physical_address}"
    if isinstance(parent_phys, dict) and parent_phys.get("pc_logic_parent_address"):
        # ``1A`` → "Key A on {LM,MI}-INPUT N", ``1B`` → "Key B on …".
        slot = parent_phys.get("pc_logic_slot_index")
        if (
            len(key_label) == 2
            and key_label[1].isalpha()
            and isinstance(slot, int)
        ):
            prefix = input_label_prefix(parent_phys)
            return f"Key {key_label[1].upper()} on {prefix}-INPUT {slot}"
    return op_point.get("description") or f"Push button {key_label}"


class NikobusPcLinkInventoryButton(ButtonEntity):
    """Bridge button that starts a PC Link inventory discovery."""

    _attr_has_entity_name = True
    _attr_translation_key = "discover_modules_buttons"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_pc_link_inventory_button"
        self._attr_device_info = hub_device_info()

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


class NikobusModuleScanButton(ButtonEntity):
    """Bridge button that starts a full module scan for button links.

    Greyed out in the UI until at least one output-capable module is
    known — the scan walks the list of known modules, so it has nothing
    to do before a PC Link inventory (or legacy-file migration) has
    populated storage.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "scan_all_module_links"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_module_scan_button"
        self._attr_device_info = hub_device_info()

    @property
    def available(self) -> bool:
        return self._coordinator.has_known_output_modules

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
        self.hass.async_create_background_task(
            self._coordinator.start_module_scan(),
            name="nikobus_module_scan_discovery",
        )


class NikobusImportNkbNamesButton(ButtonEntity):
    """Bridge button that imports device/entity names from a ``.nkb`` file.

    Reads the Nikobus PC-software project export (a ``.nkb`` placed in the
    HA config dir) and applies its module / button / IR-receiver names as
    suggested device and entity names. Non-destructive — manual renames
    are preserved.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "import_nkb_names"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_import_nkb_names_button"
        self._attr_device_info = hub_device_info()

    async def async_press(self) -> None:
        """Import names from the ``.nkb`` in the config dir.

        Awaited directly (not backgrounded): the parse runs in an
        executor and the registry writes are fast, so this returns
        quickly while still surfacing not-found / parse errors to the
        user as the button action result.
        """
        _LOGGER.info("Importing Nikobus names from .nkb via UI button")
        # The button is the quick path: import everything, non-destructive.
        result = await self._coordinator.async_import_nkb_names()
        _LOGGER.info(
            "Nikobus .nkb import done: %s devices, %s entities, %s channels, "
            "%s areas, %s scenes named",
            result["devices"],
            result["entities"],
            result.get("channels", 0),
            result["areas"],
            result["scenes"],
        )


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Representation of a Nikobus operation-point (software trigger).

    One entity per ``(physical_address, key_label)`` pair; grouped under the
    physical-button device in the registry.
    """

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