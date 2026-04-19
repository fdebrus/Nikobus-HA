"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
import re
from os.path import commonprefix
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BRAND, DOMAIN, HUB_IDENTIFIER
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

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

    if coordinator.dict_button_data:
        buttons = coordinator.dict_button_data.get("nikobus_button", {})
        register_wall_button_devices(hass, entry, buttons)
        entities.extend(
            NikobusButtonEntity(coordinator, addr, data)
            for addr, data in buttons.items()
        )

    async_add_entities(entities)


def register_wall_button_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    buttons: dict[str, Any],
) -> None:
    """Register one device per physical wall button (linked_button address).

    Groups the 1..N software buttons of a keypad/IR remote under a single
    parent device in the device registry. Idempotent: safe to call from
    multiple platforms.
    """
    device_registry = dr.async_get(hass)

    # Pass 1: bucket soft-button descriptions by wall-button address.
    grouped: dict[str, dict[str, Any]] = {}
    for data in buttons.values():
        if not isinstance(data, dict):
            continue
        desc = str(data.get("description", "") or "")
        for info in data.get("linked_button") or []:
            if not isinstance(info, dict):
                continue
            address = info.get("address")
            if not address:
                continue
            bucket = grouped.setdefault(address, {"info": info, "children": []})
            if desc:
                bucket["children"].append(desc)

    # Pass 2: register each wall-button device with a unique, meaningful name.
    for address, bucket in grouped.items():
        info = bucket["info"]
        model = info.get("model") or info.get("type") or "Wall Button"
        name = _wall_button_display_name(info, bucket["children"])
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, address)},
            manufacturer=BRAND,
            name=name,
            model=model,
            via_device=(DOMAIN, HUB_IDENTIFIER),
        )


def _wall_button_display_name(info: dict[str, Any], child_descriptions: list[str]) -> str:
    """Derive a unique, human-friendly name for a wall-button parent device.

    Uses the longest common prefix of the soft-button descriptions so a keypad
    whose children are ``BT_GF_Living_Sofa_Wall_Light_Up`` / ``…_Down`` /
    ``…_Shutter_Open`` / ``…_Close`` is named ``BT_GF_Living_Sofa``. Falls back
    to ``{type} ({address})`` when no useful prefix exists.
    """
    address = str(info.get("address") or "")
    type_str = str(info.get("type") or info.get("model") or "Wall Button")

    # Drop placeholder descriptions Nikobus auto-generates for unknown buttons:
    # "DISCOVERED - Nikobus Button #NABCDEF", "Button with 8 Operation Points #NABCDEF",
    # "Feedback Button with 4 Operation Points #NABCDEF", etc.
    meaningful = [
        d for d in child_descriptions
        if d
        and not d.startswith("DISCOVERED")
        and "UndefinedType" not in d
        and not re.search(r"#N[0-9A-Fa-f]+$", d)
    ]
    if len(meaningful) == 1:
        return meaningful[0]
    if len(meaningful) >= 2:
        prefix = commonprefix(meaningful)
        # If the common prefix stops mid-word ("BT_FF_Office_Light_O" from
        # "…_On" / "…_Off"), trim back to the last natural separator.
        if prefix and prefix[-1] not in " _-·/":
            idx = max(prefix.rfind("_"), prefix.rfind("-"), prefix.rfind(" "))
            if idx >= 3:
                prefix = prefix[:idx]
        prefix = prefix.rstrip(" _-·/")
        # Require at least a couple of semantic chunks to avoid meaningless
        # single-letter prefixes like "B" across unrelated buttons.
        if prefix.count("_") >= 2 or len(prefix) >= 8:
            return prefix

    return f"{type_str} ({address})" if address else type_str


def _hub_device_info() -> dr.DeviceInfo:
    return dr.DeviceInfo(
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        name="Nikobus Bridge",
        manufacturer=BRAND,
        model="PC-Link Bridge",
    )


class NikobusPcLinkInventoryButton(ButtonEntity):
    """Bridge button that starts a PC Link inventory discovery."""

    _attr_has_entity_name = True
    _attr_name = "Discover modules & buttons"
    _attr_icon = "mdi:magnify-scan"
    _attr_should_poll = False

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_pc_link_inventory_button"
        self._attr_device_info = _hub_device_info()

    async def async_press(self) -> None:
        """Start PC Link inventory discovery."""
        _LOGGER.info("PC Link inventory discovery triggered via UI button")
        await self._coordinator.start_pc_link_inventory()


class NikobusModuleScanButton(ButtonEntity):
    """Bridge button that starts a full module scan for button links."""

    _attr_has_entity_name = True
    _attr_name = "Scan all module links"
    _attr_icon = "mdi:cog-sync"
    _attr_should_poll = False

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_module_scan_button"
        self._attr_device_info = _hub_device_info()

    async def async_press(self) -> None:
        """Scan all output modules for button links."""
        _LOGGER.info("Module scan discovery triggered via UI button")
        await self._coordinator.start_module_scan()


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Representation of a Nikobus UI button (Software trigger)."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        data: dict[str, Any]
    ) -> None:
        """Initialize the button entity."""

        raw_desc = str(data.get("description", ""))
        name = raw_desc if raw_desc and "UndefinedType" not in raw_desc else f"Button {address}"

        wall_info = coordinator.get_wall_button_info(address)
        via_device = (DOMAIN, wall_info["address"]) if wall_info else (DOMAIN, HUB_IDENTIFIER)

        super().__init__(
            coordinator=coordinator,
            address=address,
            name=name,
            model="Push Button",
            via_device=via_device,
        )

        # Unique ID for the Home Assistant entity registry
        self._attr_unique_id = f"{DOMAIN}_push_button_{address}"
        self._operation_time = data.get("operation_time")
        self._wall_button = wall_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose wall-button parent info and linked module outputs."""
        parent_attrs = super().extra_state_attributes or {}
        attrs: dict[str, Any] = {
            **parent_attrs,
            "linked_outputs": self.coordinator.get_button_linked_outputs(self._address),
        }
        if self._wall_button:
            attrs["wall_button_address"] = self._wall_button.get("address")
            attrs["wall_button_model"] = self._wall_button.get("model")
            attrs["wall_button_type"] = self._wall_button.get("type")
            attrs["wall_button_key"] = self._wall_button.get("key")
        return attrs

    async def async_press(self) -> None:
        """Execute the button press command on the Nikobus bus."""
        _LOGGER.debug("UI Button pressed for address: %s", self._address)
        
        await self.coordinator.async_event_handler("ha_button_pressed", {
            "address": self._address,
            "operation_time": self._operation_time,
        })

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Stateless entity: Ignore general coordinator updates to reduce log noise.
        This prevents the "Targeted refresh received" log for buttons during polling.
        """
        pass