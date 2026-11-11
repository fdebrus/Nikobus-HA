"""Base classes of the entities.

An output entity is a view of one byte of its module's state: it keeps
nothing itself, so there is no optimistic value to reconcile with what
the bus reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_USE_AS,
    DIMMER_MODULE,
    DOMAIN,
    MANUFACTURER,
    ROLLER_MODULE,
    SWITCH_MODULE,
    USE_AS_LIGHT,
    USE_AS_SWITCH,
)

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


def output_platform(hub: NikobusHub, module: OutputModule, channel: int) -> Platform:
    """What an output is in Home Assistant: its module's kind, unless the options say otherwise."""
    key = f"{module.address}:{channel}"
    if module.module_type == DIMMER_MODULE:
        return Platform.FAN if key in hub.fan_outputs else Platform.LIGHT
    if module.module_type == SWITCH_MODULE:
        return Platform.LIGHT if key in hub.light_outputs else Platform.SWITCH
    use_as = hub.shutters.get(key, {}).get(CONF_USE_AS)
    return {USE_AS_SWITCH: Platform.SWITCH, USE_AS_LIGHT: Platform.LIGHT}.get(use_as, Platform.COVER)


def outputs_of(hub: NikobusHub, platform: Platform) -> list[tuple[OutputModule, int]]:
    """The outputs a platform creates entities for, and drops the moved ones' old entities.

    An output the options moved to another platform keeps its unique id
    there; the entity it had here is removed from the registry.
    """
    registry = er.async_get(hub.hass)
    mine: list[tuple[OutputModule, int]] = []
    for module in hub.inventory.output_modules():
        for channel in range(1, module.channels + 1):
            if output_platform(hub, module, channel) is platform:
                mine.append((module, channel))
            elif entity_id := registry.async_get_entity_id(platform, DOMAIN, f"{module.address}_{channel}"):
                registry.async_remove(entity_id)
    return mine


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
        self._attr_extra_state_attributes = {
            "controlled_by": [inventory.key_label(key) for key in inventory.keys_driving(module.address, channel)]
        }
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


class OnOffEntity(ChannelEntity):
    """An output that is on or off: a relay, or a roller output wired to a load.

    A relay is closed with 0xFF. A roller output has two relays, of which
    the first is the one a load is wired to: it is closed with 0x01.
    """

    def __init__(self, hub: NikobusHub, module: OutputModule, channel: int) -> None:
        super().__init__(hub, module, channel)
        self._on_value = 0x01 if module.module_type == ROLLER_MODULE else 0xFF

    @property
    def is_on(self) -> bool | None:
        """Whether the output is on; unknown until its group was heard."""
        value = self._value
        return None if value is None else value != 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Close the relay."""
        await self.hub.async_set_output(self._address, self._channel, self._on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open the relay."""
        await self.hub.async_set_output(self._address, self._channel, 0x00)
