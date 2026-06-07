"""Binary sensor platform for the Nikobus integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .button import op_point_display_name, register_wall_button_devices
from .const import DOMAIN, press_signal
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .router import iter_operation_points
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Seconds before returning to idle
STATE_RESET_DELAY = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus button sensor entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data

    buttons = (coordinator.dict_button_data or {}).get("nikobus_button", {})
    register_wall_button_devices(hass, entry, buttons, coordinator.dict_module_data)

    entities: list[NikobusButtonBinarySensor] = [
        NikobusButtonBinarySensor(
            coordinator, physical_addr, key_label, op_point, parent_phys=phys
        )
        for physical_addr, key_label, op_point, phys in iter_operation_points(buttons)
    ]
    async_add_entities(entities)


class NikobusButtonBinarySensor(NikobusEntity, BinarySensorEntity):
    """Binary sensor representing a physical Nikobus button press.

    One entity per ``(physical_address, key_label)`` pair; grouped under the
    physical-button device in the registry.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        physical_address: str,
        key_label: str,
        op_point: dict[str, Any],
        *,
        parent_phys: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the button binary sensor."""
        bus_addr = op_point["bus_address"]
        self._physical_address = physical_address
        self._key_label = key_label
        name = op_point_display_name(
            physical_address, key_label, op_point, parent_phys=parent_phys
        )
        is_pc_logic_input = isinstance(parent_phys, dict) and parent_phys.get(
            "pc_logic_parent_address"
        )
        model = "PC-Logic Key" if is_pc_logic_input else "Physical Button"
        super().__init__(
            coordinator=coordinator,
            address=bus_addr,
            name=name,
            model=model,
            via_device=(DOMAIN, physical_address),
        )
        self._attr_unique_id = f"{DOMAIN}_button_{bus_addr}"

        self._attr_is_on = False
        self._reset_timer_cancel: CALLBACK_TYPE | None = None

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
        scene = self.coordinator.get_scene_for_address(self._address)
        if scene:
            members = len(scene.get("outputs") or [])
            attrs["triggers_scene"] = f"Nikobus scene {self._address} ({members} ch)"
        return attrs

    @property
    def state(self) -> str:
        """Report ``pressed`` / ``idle`` rather than the binary_sensor
        default ``on`` / ``off``.

        Deliberately non-standard: a Nikobus button is momentary, so
        ``pressed`` / ``idle`` reads better in the UI and in automations
        (``to: "pressed"``) than ``on`` / ``off``. ``is_on`` still tracks
        the underlying boolean for anything that needs it.
        """
        return "pressed" if self._attr_is_on else "idle"

    async def async_added_to_hass(self) -> None:
        """Register event listeners when added to Home Assistant."""
        await super().async_added_to_hass()

        # Per-address signal: only this button's sensor is woken on its
        # own press, instead of a global EVENT_BUTTON_PRESSED listener
        # that every button sensor runs and filters by address.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, press_signal(self._address), self._handle_button_event
            )
        )

        def _cancel_reset_timer() -> None:
            if self._reset_timer_cancel:
                self._reset_timer_cancel()
                self._reset_timer_cancel = None

        self.async_on_remove(_cancel_reset_timer)

    @callback
    def _handle_button_event(self, data: dict) -> None:
        """This button was pressed (routed by address) — pulse to 'pressed'."""
        _LOGGER.debug("Button %s pressed", self._address)

        self._attr_is_on = True
        self.async_write_ha_state()

        # Cancel any existing timer before starting a new one
        if self._reset_timer_cancel:
            self._reset_timer_cancel()

        # Automatically return to 'idle' after the defined delay
        self._reset_timer_cancel = async_call_later(
            self.hass, STATE_RESET_DELAY, self._reset_state
        )

    @callback
    def _reset_state(self, _: datetime) -> None:
        """Reset the sensor state to 'idle'."""
        self._attr_is_on = False
        self._reset_timer_cancel = None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Ignore coordinator updates as this sensor is event-driven."""
        pass