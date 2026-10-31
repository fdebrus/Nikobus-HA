"""Binary sensor platform for the Nikobus integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from nikobus_connect.discovery.protocol import motion_detector_bus_addresses

from .button import (
    _category_for_button_type,
    op_point_display_name,
    register_wall_button_devices,
)
from .const import DOMAIN, press_signal
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .devices import wall_button_device_name
from .entity import NikobusEntity
from .router import (
    MOTION_SENSOR_KINDS,
    audio_trigger_naming,
    calendar_channel_naming,
    enabled_button_classes,
    iter_motion_detectors,
    iter_operation_points,
    motion_sensor_unique_id,
    op_point_parent_device,
)

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
    classes = enabled_button_classes(entry.options)
    register_wall_button_devices(
        hass, entry, buttons, coordinator.dict_module_data, classes=classes
    )

    entities: list[BinarySensorEntity] = [
        NikobusButtonBinarySensor(
            coordinator, physical_addr, key_label, op_point, parent_phys=phys
        )
        for physical_addr, key_label, op_point, phys in iter_operation_points(
            buttons, classes
        )
        if calendar_channel_naming(phys) is None  # never pressed on the bus
    ]

    # Motion detectors: an occupancy and a light sensor each, whatever
    # press-entity classes are selected, so the detector's device is
    # registered here too (the press platforms skip it when its class
    # is off).
    detectors = dict(iter_motion_detectors(buttons))
    if detectors:
        register_wall_button_devices(
            hass, entry, detectors, coordinator.dict_module_data, classes=None
        )
    for physical_addr, phys in detectors.items():
        frames = motion_detector_bus_addresses(physical_addr)
        for kind, on_role, off_role in MOTION_SENSOR_KINDS:
            entities.append(
                NikobusMotionDetectorSensor(
                    coordinator,
                    physical_addr,
                    phys,
                    kind=kind,
                    on_address=frames[on_role],
                    off_address=frames[off_role],
                )
            )
    async_add_entities(entities)


class NikobusMotionDetectorSensor(NikobusEntity, BinarySensorEntity, RestoreEntity):
    """Occupancy or light level of a motion detector with Nikobus interface.

    The detector sends a frame when its state changes and cannot be
    asked for it: one frame turns the sensor on, the other off, and the
    last state is restored after a restart (unknown until the first
    frame on a new install).

    * ``occupancy`` — the detector's output: on when it detects motion
      below its light threshold (or its own button is pressed), off when
      its run time has elapsed.
    * ``light`` — the light level at the sensor, against the threshold
      set on its potentiometer: on above it, off below. Room lighting
      counts as well as daylight.
    """

    _DEVICE_CLASSES: ClassVar[dict[str, BinarySensorDeviceClass]] = {
        "occupancy": BinarySensorDeviceClass.OCCUPANCY,
        "light": BinarySensorDeviceClass.LIGHT,
    }

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        physical_address: str,
        phys: Mapping[str, Any],
        *,
        kind: str,
        on_address: str,
        off_address: str,
    ) -> None:
        type_str = str(phys.get("type") or phys.get("model") or "Motion detector")
        super().__init__(
            coordinator=coordinator,
            address=physical_address,
            name=wall_button_device_name(physical_address, phys),
            model=str(phys.get("model") or type_str),
            via_device=(DOMAIN, _category_for_button_type(type_str)),
        )
        self._kind = kind
        self._on_address = on_address.upper()
        self._off_address = off_address.upper()
        self._attr_device_class = self._DEVICE_CLASSES[kind]
        self._attr_translation_key = f"motion_{kind}"
        self._attr_unique_id = motion_sensor_unique_id(physical_address, kind)
        self._attr_is_on: bool | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parent = super().extra_state_attributes or {}
        return {**parent, "on_address": self._on_address, "off_address": self._off_address}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        for address in (self._on_address, self._off_address):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, press_signal(address), self._handle_frame
                )
            )

    @callback
    def _handle_frame(self, data: dict[str, Any]) -> None:
        """The detector reported a change (each frame comes several times)."""
        address = str(data.get("address") or "").upper()
        if address == self._on_address:
            state = True
        elif address == self._off_address:
            state = False
        else:
            return
        if state != self._attr_is_on:
            self._attr_is_on = state
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Polls say nothing about the detector; only its frames do."""


class NikobusButtonBinarySensor(NikobusEntity, BinarySensorEntity):
    """Binary sensor representing a physical Nikobus button press.

    One entity per ``(physical_address, key_label)`` pair; grouped under the
    physical-button device in the registry.

    Categorised DIAGNOSTIC (3.13.0), like the press-simulation buttons:
    press pulses are automation signals, not room controls — keeping
    them out of the Controls sections and auto-dashboards leaves the
    real outputs as the visible surface. Automations targeting these
    sensors are unaffected.
    """

    _attr_entity_registry_enabled_default = False
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
        if is_pc_logic_input:
            model = "PC-Logic Key"
        elif parent_phys is not None and audio_trigger_naming(parent_phys):
            model = "Audio Trigger"
        else:
            model = "Physical Button"
        super().__init__(
            coordinator=coordinator,
            address=bus_addr,
            name=name,
            model=model,
            via_device=op_point_parent_device(physical_address, bus_addr, parent_phys),
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
    def _handle_button_event(self, data: dict[str, Any]) -> None:
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
