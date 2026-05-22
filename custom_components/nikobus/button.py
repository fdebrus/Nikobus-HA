"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
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
    HUB_IDENTIFIER,
    SIGNAL_DISCOVERY_STATE,
)
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity
from .router import INPUT_MODULE_TYPES, OPAQUE_MODULE_TYPES

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
    ]

    buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
    register_wall_button_devices(hass, entry, buttons, coordinator.dict_module_data)
    entities.extend(_iter_button_entities(coordinator, buttons))

    # Input-class modules (PC-Logic, Modular Interface) — register one
    # device per module address and one button entity per channel.
    register_input_module_devices(hass, entry, coordinator.dict_module_data)
    entities.extend(_iter_input_module_entities(coordinator))

    # Opaque modules (Audio Distribution) — register the device so it's
    # visible in HA's device registry, but don't create any entities
    # for it yet (input/output schema not validated).
    register_opaque_module_devices(hass, entry, coordinator.dict_module_data)

    async_add_entities(entities)


def _iter_button_entities(
    coordinator: NikobusDataCoordinator,
    buttons: dict[str, Any],
):
    """Yield one NikobusButtonEntity per discovered operation point."""
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, dict):
            continue
        for key_label, op_point in op_points.items():
            if not isinstance(op_point, dict):
                continue
            bus_addr = op_point.get("bus_address")
            if not bus_addr:
                continue
            yield NikobusButtonEntity(
                coordinator, physical_addr, key_label, op_point, parent_phys=phys
            )


def _category_for_button_type(type_str: str) -> str:
    """Return the category device identifier appropriate for a button's type.

    Classification rule based on the discovery-supplied ``type`` field:

      * ``RF`` anywhere → Remotes (RF hand-held / RF wall transmitters)
      * ``Interface`` anywhere → Interfaces (push-button / switch /
        universal input interfaces — non-keypad input sources)
      * everything else → Wall buttons (physical bus push buttons)
    """
    lowered = type_str.lower()
    if "rf" in lowered:
        return CATEGORY_REMOTES
    if "interface" in lowered:
        return CATEGORY_INTERFACES
    return CATEGORY_WALL_BUTTONS


def _pc_logic_input_naming(
    phys: dict[str, Any],
) -> tuple[str, tuple[str, str]] | None:
    """Return ``(device_name, via_device_identifier)`` if ``phys`` is a
    synthesized PC-Logic logical-input entry; else ``None``.

    The library sets ``pc_logic_parent_address`` (the PC-Logic module
    address) and ``pc_logic_slot_index`` (1..N) on the button-store
    entry when it synthesizes virtual buttons for PC-Logic inputs. HA
    parents the device directly under the PC-Logic module device
    (instead of the wall-buttons category) and renames it
    ``LM-INPUT N`` to match Niko's own terminology.
    """

    parent_addr = phys.get("pc_logic_parent_address")
    slot = phys.get("pc_logic_slot_index")
    if not isinstance(parent_addr, str) or not isinstance(slot, int):
        return None
    return f"LM-INPUT {slot}", (DOMAIN, parent_addr.upper())


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

    Synthesized PC-Logic logical inputs (the library's
    ``_synthesize_pc_logic_inputs`` adds these with ``pc_logic_*``
    provenance fields) are routed differently: the device is parented
    directly under the PC-Logic module that owns it, and the name
    follows the Niko ``LM-INPUT N`` convention.
    """
    device_registry = dr.async_get(hass)
    pc_logic_parents_registered: set[str] = set()
    remote_transmitter_parents_registered: set[str] = set()
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue

        pc_logic_naming = _pc_logic_input_naming(phys)
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


def _hub_device_info() -> dr.DeviceInfo:
    return dr.DeviceInfo(
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        name="Nikobus Bridge",
        manufacturer=BRAND,
        model="PC-Link Bridge",
    )


def _iter_module_records(
    dict_module_data: dict[str, Any], module_types: frozenset[str]
):
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


def _iter_input_module_entities(coordinator: NikobusDataCoordinator):
    """Yield one NikobusInputEntity per channel of every input-class module.

    Both PC-Logic (05-201) and Modular Interface (05-206) inputs are
    surfaced through synthesized button-store entries (one ``LM-INPUT N``
    device per input, parented under the owning module). Their per-channel
    placeholder entities are redundant — skip the module-attached
    ``NikobusInputEntity`` creation for both.
    """
    for module_type, address, module_data in _iter_module_records(
        coordinator.dict_module_data, INPUT_MODULE_TYPES
    ):
        if module_type in ("pc_logic", "interface_module"):
            continue
        module_desc = str(
            module_data.get("description") or f"{module_type} ({address})"
        )
        module_model = str(module_data.get("model") or module_type)
        channels = module_data.get("channels") or []
        if not isinstance(channels, list):
            continue
        for channel_index, channel_info in enumerate(channels, start=1):
            if not isinstance(channel_info, dict):
                continue
            if channel_info.get("entity_type") == "disabled":
                continue
            channel_description = str(
                channel_info.get("description") or f"Input {channel_index}"
            )
            if channel_description.startswith("not_in_use"):
                continue
            yield NikobusInputEntity(
                coordinator,
                module_type=module_type,
                module_address=address,
                channel=channel_index,
                channel_description=channel_description,
                module_description=module_desc,
                module_model=module_model,
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

    PC-Logic logical-input keys (the parent button carries
    ``pc_logic_parent_address``) render as ``Key A on LM-INPUT N`` /
    ``Key B on LM-INPUT N``, mirroring the IR ``<key> on <parent>``
    pattern so the device list disambiguates each slot's keys.
    """
    if key_label.startswith("IR:"):
        ir_code = key_label[len("IR:"):]
        return f"IR {ir_code} on {physical_address}"
    if isinstance(parent_phys, dict) and parent_phys.get("pc_logic_parent_address"):
        # ``1A`` → "Key A on LM-INPUT N", ``1B`` → "Key B on LM-INPUT N".
        slot = parent_phys.get("pc_logic_slot_index")
        if (
            len(key_label) == 2
            and key_label[1].isalpha()
            and isinstance(slot, int)
        ):
            return f"Key {key_label[1].upper()} on LM-INPUT {slot}"
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
        self._attr_device_info = _hub_device_info()

    async def async_press(self) -> None:
        """Start PC Link inventory discovery."""
        _LOGGER.info("PC Link inventory discovery triggered via UI button")
        await self._coordinator.start_pc_link_inventory()


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
        self._attr_device_info = _hub_device_info()

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
        """Scan all output modules for button links."""
        _LOGGER.info("Module scan discovery triggered via UI button")
        await self._coordinator.start_module_scan()


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
            # "legacy_orphan", "legacy_undecoded", "synthesized_input".
            # Surface only the legacy flags so healthy buttons (active
            # wall buttons and synthesized PC-Logic / 05-206 inputs)
            # aren't cluttered.
            status = wall_info.get("status")
            if status in ("legacy_orphan", "legacy_undecoded"):
                attrs["wall_button_status"] = status
        return attrs

    async def async_press(self) -> None:
        """Execute the button press command on the Nikobus bus."""
        _LOGGER.debug("UI Button pressed for address: %s", self._address)
        await self.coordinator.async_event_handler(
            "ha_button_pressed", {"address": self._address}
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Stateless entity: Ignore general coordinator updates to reduce log noise.
        This prevents the "Targeted refresh received" log for buttons during polling.
        """
        pass


class NikobusInputEntity(NikobusEntity, ButtonEntity):
    """One ButtonEntity per input channel of a PC-Logic / Modular Interface.

    Input-class modules (PC-Logic 05-201, Modular Interface 05-206) feed
    six dry-contact inputs onto the Nikobus bus. Each input is surfaced as
    a stateless button so users can see the device, attach automations to
    its press events, and confirm discovery saw the module.

    TODO(input-routing): the on-bus press-frame format for LM/IM inputs
    is not yet known. Until ``coordinator.async_event_handler`` learns to
    route those frames, the UI press handler is a no-op (only logs) — it
    cannot fake a press onto the bus the way ``NikobusButtonEntity`` does.
    Library work is needed to sniff and document the press-frame layout.
    """

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        *,
        module_type: str,
        module_address: str,
        channel: int,
        channel_description: str,
        module_description: str,
        module_model: str,
    ) -> None:
        """Initialize an input-channel button entity."""
        super().__init__(
            coordinator=coordinator,
            address=module_address,
            name=module_description,
            model=module_model,
            via_device=(DOMAIN, CATEGORY_SYSTEM_MODULES),
        )
        self._module_type = module_type
        self._channel = channel
        self._channel_description = channel_description
        self._attr_name = channel_description
        self._attr_unique_id = (
            f"{DOMAIN}_input_button_{module_address}_{channel}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose channel and parent-module info."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_type": self._module_type,
        }

    async def async_press(self) -> None:
        """UI press handler — no bus action until input routing is wired.

        See class docstring TODO: the press-frame format for input-class
        modules is unknown, so we can't dispatch ``ha_button_pressed``
        with a meaningful address yet.
        """
        _LOGGER.warning(
            "UI press on Nikobus input %s ch%d (module %s) ignored — "
            "input-module press routing is not yet implemented",
            self._address,
            self._channel,
            self._module_type,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Stateless entity: ignore polling updates to reduce log noise."""
        pass