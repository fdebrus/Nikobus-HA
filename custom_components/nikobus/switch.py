"""Switch platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, EVENT_BUTTON_OPERATION, EVENT_BUTTON_PRESSED
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity
from .router import (
    build_unique_id,
    get_routing,
    input_latch_switch_unique_id,
    iter_input_module_children,
    pc_logic_input_naming,
    register_output_module_devices,
)

try:  # nikobus-connect provides the input-address math
    from nikobus_connect.discovery.protocol import (
        convert_nikobus_address,
        derive_pc_logic_input_physicals,
    )
except ImportError:  # pragma: no cover - defensive (older library)
    convert_nikobus_address = None
    derive_pc_logic_input_physicals = None

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def input_ab_addresses(phys: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(addr_1A, addr_1B)`` bus addresses for a synthesized
    PC-Logic / Modular-Interface input, or ``None``.

    A PC-Logic logical input emits two bus events — its 1A and 1B
    forms. Validated against two installs (and the library's own
    derivation note):

      * ``1A = convert_nikobus_address(physical)``
      * ``1B`` = ``1A`` with the first hex nibble incremented by 4

    The physical address is re-derived from the input's
    ``pc_logic_parent_address`` + ``pc_logic_slot_index`` provenance so
    this doesn't depend on the button-store key format.
    """

    if convert_nikobus_address is None or derive_pc_logic_input_physicals is None:
        return None
    parent = phys.get("pc_logic_parent_address")
    slot = phys.get("pc_logic_slot_index")
    if not isinstance(parent, str) or not isinstance(slot, int) or slot < 1:
        return None
    try:
        # Deriving exactly ``slot`` physicals yields the slot we want at
        # ``[slot - 1]``; the library raises for an out-of-range slot.
        physical = derive_pc_logic_input_physicals(parent, slot)[slot - 1]
    except (ValueError, IndexError):  # pragma: no cover - defensive
        return None
    addr_1a = convert_nikobus_address(physical)
    if len(addr_1a) != 6:  # convert returns a "[...]" marker when it can't
        return None
    addr_1a = addr_1a.upper()
    addr_1b = format((int(addr_1a[0], 16) + 4) % 16, "X") + addr_1a[1:]
    return addr_1a, addr_1b


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus switch entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = []

    routing = get_routing(hass, entry, coordinator.dict_module_data)
    specs = routing.get("switch", [])
    register_output_module_devices(hass, entry, specs)

    for spec in specs:
        if spec.kind == "relay_switch":
            entities.append(
                NikobusRelaySwitchEntity(
                    coordinator, spec.address, spec.channel,
                    spec.channel_description, spec.module_desc, spec.module_model
                )
            )
        elif spec.kind == "cover_binary":
            entities.append(
                NikobusCoverSwitchEntity(
                    coordinator, spec.address, spec.channel,
                    spec.channel_description, spec.module_desc, spec.module_model
                )
            )

    # Stateful A/B latch switch per PC-Logic / Modular-Interface input.
    # The input itself surfaces as a stateless button (button.py); this
    # adds a persistent on/off mirror: the 1A signal turns it on, 1B
    # turns it off, and turn_on/off drive the corresponding bus frame.
    for physical_addr, phys in iter_input_module_children(
        coordinator.dict_button_data.get("nikobus_button", {})
    ):
        naming = pc_logic_input_naming(phys)
        ab = input_ab_addresses(phys)
        if naming is None or ab is None:
            continue
        device_name, via_device = naming
        addr_1a, addr_1b = ab
        entities.append(
            NikobusInputLatchSwitch(
                coordinator,
                physical_addr=str(physical_addr).upper(),
                addr_1a=addr_1a,
                addr_1b=addr_1b,
                device_name=device_name,
                via_device=via_device,
                model=str(phys.get("model") or "Logical Input"),
            )
        )

    async_add_entities(entities)


class NikobusBaseSwitch(NikobusEntity, SwitchEntity, RestoreEntity):
    """Base class for Nikobus switch entities with hybrid update logic."""

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize the switch base."""
        super().__init__(coordinator, address, module_name, module_model)
        self._address = address
        self._channel = channel
        self._channel_description = description
        self._module_description = module_name
        self._module_model = module_model
        
        self._attr_name = description
        self._is_on: bool | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes safely."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "nikobus_address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._module_description,
            "module_model": self._module_model,
            "controlled_by": self.coordinator.get_controlled_by(self._address, self._channel),
        }

    async def async_added_to_hass(self) -> None:
        """Register listeners and restore state."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            self._is_on = last_state.state == "on"

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_OPERATION, self._handle_nikobus_event)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate cache when hardware data is received."""
        self._is_on = None
        super()._handle_coordinator_update()

    @callback
    def _handle_nikobus_event(self, event: Event) -> None:
        """Handle physical button operation events."""
        if str(event.data.get("impacted_module_address")) != str(self._address):
            return
        
        self._is_on = None
        self.async_write_ha_state()


class NikobusRelaySwitchEntity(NikobusBaseSwitch):
    """Standard Nikobus relay-based on/off switch."""

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize relay switch."""
        super().__init__(coordinator, address, channel, description, module_name, module_model)
        self._attr_unique_id = build_unique_id("switch", "relay_switch", self._address, self._channel)

    @property
    def is_on(self) -> bool:
        """Return optimistic state if set, else coordinator state."""
        if self._is_on is not None:
            return self._is_on
        return self.coordinator.get_switch_state(self._address, self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Close relay with optimistic UI update and error fallback."""
        self._is_on = True
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.turn_on_switch(self._address, self._channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open relay with optimistic UI update and error fallback."""
        self._is_on = False
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.turn_off_switch(self._address, self._channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise err


class NikobusCoverSwitchEntity(NikobusBaseSwitch):
    """Binary switch entity driving a cover channel."""

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize cover-as-switch."""
        super().__init__(coordinator, address, channel, description, module_name, module_model)
        self._attr_unique_id = build_unique_id("switch", "cover_binary", self._address, self._channel)

    @property
    def is_on(self) -> bool:
        """Return optimistic state if set, else coordinator state."""
        if self._is_on is not None:
            return self._is_on
        return self.coordinator.get_cover_state(self._address, self._channel) == 0x01

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Trigger 'Open' on cover module with optimistic UI update and error fallback."""
        self._is_on = True
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.open_cover(self._address, self._channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Trigger 'Stop/Close' on cover module with optimistic UI update and error fallback."""
        self._is_on = False
        self.async_write_ha_state()
        
        try:
            await self.coordinator.api.stop_cover(self._address, self._channel, direction="closing")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._is_on = None
            self.async_write_ha_state()
            raise err

class NikobusInputLatchSwitch(NikobusEntity, SwitchEntity, RestoreEntity):
    """Stateful on/off latch for a PC-Logic / Modular-Interface input.

    A PC-Logic logical input is momentary on the bus: it emits a 1A
    pulse and a 1B pulse rather than holding a level. This entity gives
    that input a persistent on/off state in HA:

      * the **1A** bus signal latches it **on**, **1B** latches it
        **off** (observed via the ``nikobus_button_pressed`` event, so
        physical presses and other controllers are tracked too);
      * ``turn_on`` / ``turn_off`` drive the corresponding 1A / 1B bus
        frame (same ``#N<addr>\\r#E1`` path as a wall-button simulation).

    State is restored across restarts. It lives alongside the stateless
    input button entity, sharing the same input device.
    """

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        *,
        physical_addr: str,
        addr_1a: str,
        addr_1b: str,
        device_name: str,
        via_device: tuple[str, str],
        model: str,
    ) -> None:
        super().__init__(
            coordinator=coordinator,
            address=physical_addr,
            name=device_name,
            model=model,
            via_device=via_device,
        )
        self._addr_1a = addr_1a
        self._addr_1b = addr_1b
        self._attr_name = "A/B state"
        self._attr_unique_id = input_latch_switch_unique_id(physical_addr)
        self._attr_is_on = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parent = super().extra_state_attributes or {}
        return {**parent, "address_1a": self._addr_1a, "address_1b": self._addr_1b}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESSED, self._handle_button_event)
        )

    @callback
    def _handle_button_event(self, event: Event) -> None:
        """Latch on the 1A signal, clear on the 1B signal."""
        addr = str(event.data.get("address") or "").upper()
        if addr == self._addr_1a:
            self._attr_is_on = True
            self.async_write_ha_state()
        elif addr == self._addr_1b:
            self._attr_is_on = False
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_event_handler(
            "ha_button_pressed", {"address": self._addr_1a}
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_event_handler(
            "ha_button_pressed", {"address": self._addr_1b}
        )
        self._attr_is_on = False
        self.async_write_ha_state()
