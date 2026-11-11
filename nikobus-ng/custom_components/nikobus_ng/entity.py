"""Base classes of the entities.

An output entity is a view of one byte of its module's state: it keeps
nothing itself, so there is no optimistic value to reconcile with what
the bus reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, SWITCH_MODULE

if TYPE_CHECKING:
    from .hub import NikobusHub
    from .inventory import OutputModule

_GATEWAY_MODELS = {"pc_link": "PC-Link", "feedback_module": "Feedback module", "pc_logic": "PC-Logic"}


def gateway_device(hub: NikobusHub) -> DeviceInfo:
    """The device every other device of the entry hangs under."""
    return DeviceInfo(
        identifiers={(DOMAIN, hub.entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=_GATEWAY_MODELS.get(hub.connection.gateway_family or "", "Gateway"),
        name="Nikobus",
        serial_number=hub.connection.gateway_address,
    )


def plate_device(hub: NikobusHub, address: str, entry: dict[str, Any]) -> DeviceInfo:
    """The device of a plate, a transmitter or a detector."""
    description = str(entry.get("description") or entry.get("type") or "Nikobus button").strip()
    return DeviceInfo(
        identifiers={(DOMAIN, address)},
        manufacturer=MANUFACTURER,
        model=entry.get("model"),
        name=hub.inventory.device_name(address) or description,
        suggested_area=hub.inventory.device_area(address),
        via_device_id=hub.gateway_device_id,
    )


def relay_outputs(hub: NikobusHub, *, as_light: bool) -> list[tuple[OutputModule, int]]:
    """The relays shown as lights, or the ones left as switches."""
    return [
        (module, channel)
        for module in hub.inventory.output_modules()
        if module.module_type == SWITCH_MODULE
        for channel in range(1, module.channels + 1)
        if (f"{module.address}:{channel}" in hub.light_outputs) is as_light
    ]


def remove_relay_entities(hass: HomeAssistant, domain: str, outputs: list[tuple[OutputModule, int]]) -> None:
    """Remove the entity a relay had in ``domain`` before the options moved it."""
    registry = er.async_get(hass)
    for module, channel in outputs:
        if entity_id := registry.async_get_entity_id(domain, DOMAIN, f"{module.address}_{channel}"):
            registry.async_remove(entity_id)


class NikobusEntity(Entity):
    """Anything that needs the bus to be there."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: NikobusHub) -> None:
        self.hub = hub

    @property
    def available(self) -> bool:
        """Whether the gateway is connected."""
        return self.hub.connected


class GatewayEntity(NikobusEntity):
    """An entity of the gateway itself, woken by its status changes."""

    def __init__(self, hub: NikobusHub, key: str) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = gateway_device(hub)

    async def async_added_to_hass(self) -> None:
        """Follow the connection and the discovery."""
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))


class ChannelEntity(NikobusEntity):
    """One output of a switch, dimmer or roller module."""

    _attr_translation_key = "output"

    def __init__(self, hub: NikobusHub, module: OutputModule, channel: int) -> None:
        super().__init__(hub)
        self._address = module.address
        self._channel = channel
        self._attr_unique_id = f"{module.address}_{channel}"
        inventory = hub.inventory
        if name := inventory.channel_name(module.address, channel):
            self._attr_name = name
        else:
            self._attr_translation_placeholders = {"channel": str(channel)}
        # An output no key drives, on a module whose links are all known,
        # is most likely not wired: it is there, but not enabled.
        self._attr_entity_registry_enabled_default = (
            name is not None
            or not inventory.links_read(module.address)
            or inventory.is_linked(module.address, channel)
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module.address)},
            manufacturer=MANUFACTURER,
            model=module.model or None,
            name=inventory.device_name(module.address) or f"{module.description} {module.address}",
            suggested_area=inventory.device_area(module.address),
            via_device_id=hub.gateway_device_id,
        )

    async def async_added_to_hass(self) -> None:
        """Follow the module's state."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.subscribe(self._address, self._on_module_update))

    def _on_module_update(self) -> None:
        self.async_write_ha_state()

    @property
    def _value(self) -> int | None:
        """The output's byte; ``None`` until its group was heard."""
        return self.hub.value(self._address, self._channel)
