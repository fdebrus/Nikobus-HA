"""Button platform for the Nikobus integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_PRIOR_GEN3
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus button entities from a config entry."""
    _LOGGER.debug("Setting up Nikobus button entities.")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[NikobusButtonEntity] = []

    if coordinator.dict_button_data:
        for button_data in coordinator.dict_button_data.get(
            "nikobus_button", {}
        ).values():
            impacted_modules_info = [
                {"address": module["address"], "group": module["group"]}
                for module in button_data.get("impacted_module", [])
            ]

            # Extract the discovered info from the list if available.
            discovery_info = button_data.get("discovered_info", [])
            discovery_info = discovery_info[0] if discovery_info else {}

            entity = NikobusButtonEntity(
                coordinator=coordinator,
                config_entry=entry,
                description=button_data.get("description", "Unknown Button"),
                address=button_data.get("address", "unknown"),
                operation_time=button_data.get("operation_time"),
                impacted_modules_info=impacted_modules_info,
                discovery_type=discovery_info.get("type"),
                discovery_model=discovery_info.get("model"),
                discovery_address=discovery_info.get("address"),
                discovery_channel=discovery_info.get("channels"),
                discovery_key=discovery_info.get("key"),
            )
            entities.append(entity)

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus button entities.", len(entities))


class NikobusButtonEntity(NikobusEntity, ButtonEntity):
    """Represents a Nikobus button entity within Home Assistant."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        config_entry: ConfigEntry,
        description: str,
        address: str,
        operation_time: int | None,
        impacted_modules_info: list[dict[str, Any]],
        discovery_type: str,
        discovery_model: str,
        discovery_address: str,
        discovery_channel: str,
        discovery_key: str,
    ) -> None:
        """Initialize the button entity with data from the Nikobus system configuration."""
        normalized_address = address.strip().upper()
        super().__init__(
            coordinator=coordinator,
            address=normalized_address,
            name=description,
            model=discovery_model or "Push Button",
        )
        self._coordinator = coordinator
        self._operation_time = int(operation_time) if operation_time else None
        self.impacted_modules_info = impacted_modules_info
        self.discovery_type = discovery_type
        self.discovery_model = discovery_model
        self.discovery_address = discovery_address
        self.discovery_channel = discovery_channel
        self.discovery_key = discovery_key
        self._last_press_type: str | None = None
        self._last_press_source: str | None = None
        self._last_press_timestamp: str | None = None
        self._last_press_address: str | None = None
        self._last_press_channel: int | None = None
        self._last_press_module_address: str | None = None
        self._last_press_event_type: str | None = None
        self._last_press_raw: dict[str, Any] | None = None
        self._last_press_id: str | None = None
        self._unsub_button_events: list[Callable[[], None]] = []

        self._attr_name = f"Nikobus Push Button {normalized_address}"
        self._attr_unique_id = f"{DOMAIN}_push_button_{normalized_address}"
        self._attr_last_pressed = None

        # Option set in the config entry
        self._prior_gen3: bool = config_entry.data.get(
            CONF_PRIOR_GEN3, False
        )

    # ---------------------------------------------------------------------
    # Home Assistant entity metadata
    # ---------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        attributes = {
            "type": self.discovery_type,
            "model": self.discovery_model,
            "address": self.discovery_address,
            "channel": self.discovery_channel,
            "key": self.discovery_key,
            "operation_time": self._operation_time,
            "last_press_type": self._last_press_type,
            "last_press_source": self._last_press_source,
            "last_press_timestamp": self._last_press_timestamp,
            "last_press_address": self._last_press_address,
            "last_press_channel": self._last_press_channel,
            "last_press_module_address": self._last_press_module_address,
            "last_press_event_type": self._last_press_event_type,
            "last_press_raw": self._last_press_raw,
        }

        if self.impacted_modules_info:
            attributes["impacted_modules"] = list(self.impacted_modules_info)
            attributes["user_impacted_modules"] = ", ".join(
                f"{module['address']}_{module['group']}"
                for module in self.impacted_modules_info
            )

        return attributes

    # ---------------------------------------------------------------------
    # Button behaviour
    # ---------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Register event listeners for button press updates."""
        await super().async_added_to_hass()
        self._unsub_button_events = [
            self.hass.bus.async_listen(event, self._handle_button_event)
            for event in (
                "nikobus_button_pressed",
                "nikobus_button_released",
                "nikobus_short_button_pressed",
                "nikobus_long_button_pressed",
                "nikobus_button_operation",
            )
        ]

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listeners."""
        for unsub in self._unsub_button_events:
            unsub()
        self._unsub_button_events = []
        await super().async_will_remove_from_hass()

    async def async_press(self) -> None:
        """Handle button press event."""
        press_timestamp = datetime.now(timezone.utc).isoformat()
        module_address, channel = self._coordinator._derive_button_context(
            self._address
        )
        event_data = {
            "address": self._address,
            "operation_time": self._operation_time,
            "ts": press_timestamp,
            "source": "ha",
            "module_address": module_address,
            "channel": channel,
        }
        try:
            self._record_press(
                source="ha",
                press_type="press",
                event_type="ha_button_pressed",
                event_data=event_data,
            )
            _LOGGER.info("Processing HA button press: %s", self._address)
            await self._coordinator.async_event_handler("ha_button_pressed", event_data)

            # Skip the refresh for Gen3 installations if requested
            if not self._prior_gen3:
                for module in self.impacted_modules_info:
                    module_address, module_group = module["address"], module["group"]
                    try:
                        _LOGGER.debug(
                            "Refreshing module %s, group %s",
                            module_address,
                            module_group,
                        )
                        value = await self._coordinator.nikobus_command.get_output_state(
                            module_address, module_group
                        )
                        if value is not None:
                            self._coordinator.set_bytearray_group_state(
                                module_address, module_group, value
                            )
                            _LOGGER.debug(
                                "Updated state for module %s, group %s",
                                module_address,
                                module_group,
                            )
                        else:
                            _LOGGER.warning(
                                "No output state returned for module %s, group %s",
                                module_address,
                                module_group,
                            )
                    except Exception as inner_err:
                        _LOGGER.error(
                            "Failed to refresh module %s, group %s: %s",
                            module_address,
                            module_group,
                            inner_err,
                            exc_info=True,
                        )
        except Exception as err:
            _LOGGER.error(
                "Failed to handle button press for %s: %s",
                self._address,
                err,
                exc_info=True,
            )

    @callback
    def _handle_button_event(self, event: Any) -> None:
        """Handle button press event updates."""
        event_address = (event.data.get("address") or "").strip().upper()
        if event_address != self._address:
            return

        source = event.data.get("source")
        if source == "ha":
            return

        event_type = event.event_type
        press_type_map = {
            "nikobus_short_button_pressed": "short",
            "nikobus_long_button_pressed": "long",
            "nikobus_button_released": "release",
            "nikobus_button_pressed": "press",
            "nikobus_button_operation": "operation",
        }
        press_type = press_type_map.get(event_type, "press")

        if press_type in {"release", "operation"}:
            return

        press_id = event.data.get("press_id")
        update_last_pressed = True
        if press_type in {"short", "long"} and press_id and press_id == self._last_press_id:
            update_last_pressed = False

        self._record_press(
            source=source or "nikobus",
            press_type=press_type,
            event_type=event_type,
            event_data=event.data,
            update_last_pressed=update_last_pressed,
        )

    def _record_press(
        self,
        *,
        source: str,
        press_type: str,
        event_type: str,
        event_data: dict[str, Any],
        update_last_pressed: bool = True,
    ) -> None:
        """Update last-pressed state and emit a normalized activity event."""
        _LOGGER.debug(
            "Recording press for %s: source=%s press_type=%s event_type=%s data=%s",
            self._address,
            source,
            press_type,
            event_type,
            event_data,
        )
        timestamp = event_data.get("ts") or datetime.now(timezone.utc).isoformat()
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp)
            if parsed_timestamp.tzinfo is None:
                parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            parsed_timestamp = datetime.now(timezone.utc)
            timestamp = parsed_timestamp.isoformat()

        if update_last_pressed:
            self._attr_last_pressed = parsed_timestamp
        self._last_press_type = press_type
        self._last_press_source = source
        self._last_press_timestamp = timestamp
        self._last_press_address = (event_data.get("address") or self._address).strip().upper()
        self._last_press_channel = event_data.get("channel")
        self._last_press_module_address = event_data.get("module_address")
        self._last_press_event_type = event_type
        self._last_press_raw = dict(event_data)
        self._last_press_id = event_data.get("press_id")

        if update_last_pressed:
            activity_payload = {
                "address": self._last_press_address,
                "source": source,
                "press_type": press_type,
                "event_type": event_type,
                "timestamp": timestamp,
                "channel": self._last_press_channel,
                "module_address": self._last_press_module_address,
                "operation_time": self._operation_time,
                "impacted_modules": list(self.impacted_modules_info),
                "user_impacted_modules": ", ".join(
                    f"{module['address']}_{module['group']}"
                    for module in self.impacted_modules_info
                )
                if self.impacted_modules_info
                else None,
                "raw": dict(event_data),
            }
            self.hass.bus.async_fire("nikobus_button_activity", activity_payload)
        self.async_write_ha_state()
