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

from .const import BRAND, DOMAIN, HUB_IDENTIFIER, SIGNAL_DISCOVERY_STATE
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity

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
    register_wall_button_devices(hass, entry, buttons)
    entities.extend(_iter_button_entities(coordinator, buttons))

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
            yield NikobusButtonEntity(coordinator, physical_addr, key_label, op_point)


def register_wall_button_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    buttons: dict[str, Any],
) -> None:
    """Register one device per physical wall button (top-level address).

    Groups the N operation-points of a keypad/IR remote under a single parent
    device in the device registry. The default name is taken straight from
    the discovery metadata (``{type} ({address})``) so it is identical for
    every installation; HA preserves any user rename via ``name_by_user``
    across reloads. Idempotent: safe to call from multiple platforms.
    """
    device_registry = dr.async_get(hass)
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue
        type_str = str(phys.get("type") or phys.get("model") or "Wall Button")
        model = str(phys.get("model") or phys.get("type") or "Wall Button")
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, physical_addr)},
            manufacturer=BRAND,
            name=f"{type_str} ({physical_addr})",
            model=model,
            via_device=(DOMAIN, HUB_IDENTIFIER),
        )


def _hub_device_info() -> dr.DeviceInfo:
    return dr.DeviceInfo(
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        name="Nikobus Bridge",
        manufacturer=BRAND,
        model="PC-Link Bridge",
    )


def op_point_display_name(
    physical_address: str, key_label: str, op_point: dict[str, Any]
) -> str:
    """Build a UI-visible name for an op-point's device entry.

    IR op-points (storage keys starting with ``IR:``) get the receiver's
    bus address appended so the same IR code registered on different
    receivers remains distinguishable in the device list — the
    library-generated description ("IR code 30A #I30A") is identical for
    every receiver that learned the same code. Wall keys keep the
    library description verbatim; it already carries the channel label.
    """
    if key_label.startswith("IR:"):
        ir_code = key_label[len("IR:"):]
        return f"IR {ir_code} on {physical_address}"
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
    ) -> None:
        """Initialize the button entity."""
        bus_addr = op_point["bus_address"]
        self._physical_address = physical_address
        self._key_label = key_label

        name = op_point_display_name(physical_address, key_label, op_point)

        super().__init__(
            coordinator=coordinator,
            address=bus_addr,
            name=name,
            model="Push Button",
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