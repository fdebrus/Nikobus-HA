"""Binary sensor platform for the Nikobus integration."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .button import register_wall_button_devices
from .const import DOMAIN, EVENT_BUTTON_PRESSED, HUB_IDENTIFIER
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
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

    if not coordinator.dict_button_data:
        return

    buttons = coordinator.dict_button_data.get("nikobus_button", {})
    register_wall_button_devices(hass, entry, buttons)

    async_add_entities(
        NikobusButtonBinarySensor(
            coordinator=coordinator,
            address=address,
            description=data.get("description", f"Button {address}"),
        )
        for address, data in buttons.items()
    )


class NikobusButtonBinarySensor(NikobusEntity, BinarySensorEntity):
    """Binary sensor representing a physical Nikobus button press."""

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        description: str,
    ) -> None:
        """Initialize the button binary sensor."""
        wall_info = coordinator.get_wall_button_info(address)
        via_device = (DOMAIN, wall_info["address"]) if wall_info else (DOMAIN, HUB_IDENTIFIER)
        super().__init__(
            coordinator=coordinator,
            address=address,
            name=description,
            model="Physical Button",
            via_device=via_device,
        )
        self._address = address
        self._attr_name = description
        self._attr_unique_id = f"{DOMAIN}_button_{address}"
        self._wall_button = wall_info

        self._attr_is_on = False
        self._reset_timer_cancel: CALLBACK_TYPE | None = None

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

    @property
    def state(self) -> str:
        """Override to return 'pressed' if on, else 'idle'."""
        return "pressed" if self._attr_is_on else "idle"

    async def async_added_to_hass(self) -> None:
        """Register event listeners when added to Home Assistant."""
        await super().async_added_to_hass()
        
        # Listen directly for button press events for this specific address
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESSED, self._handle_button_event)
        )

        def _cancel_reset_timer() -> None:
            if self._reset_timer_cancel:
                self._reset_timer_cancel()
                self._reset_timer_cancel = None

        self.async_on_remove(_cancel_reset_timer)

    @callback
    def _handle_button_event(self, event: Event) -> None:
        """Handle button press events from the Nikobus bus."""
        if event.data.get("address") != self._address:
            return

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