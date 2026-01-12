"""Switch platform for the Nikobus integration with module-level devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity
from .router import build_unique_id, get_routing
from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Nikobus switch entities from a config entry."""
    _LOGGER.debug("Setting up Nikobus switch entities (modules).")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    entities: list[SwitchEntity] = []
    registered_addresses: set[str] = set()

    routing = get_routing(hass, entry, coordinator.dict_module_data)
    for spec in routing["switch"]:
        if spec.address not in registered_addresses:
            _register_nikobus_module_device(
                device_registry=device_registry,
                entry=entry,
                module_address=spec.address,
                module_name=spec.module_desc,
                module_model=spec.module_model,
            )
            registered_addresses.add(spec.address)

        if spec.kind == "relay_switch":
            entities.append(
                NikobusSwitchEntity(
                    coordinator=coordinator,
                    address=spec.address,
                    channel=spec.channel,
                    channel_description=spec.channel_description,
                    module_name=spec.module_desc,
                    module_model=spec.module_model,
                )
            )
        elif spec.kind == "cover_binary":
            entities.append(
                NikobusSwitchCoverEntity(
                    coordinator=coordinator,
                    address=spec.address,
                    channel=spec.channel,
                    channel_description=spec.channel_description,
                    module_desc=spec.module_desc,
                    module_model=spec.module_model,
                )
            )
        else:
            _LOGGER.warning(
                "Unhandled switch routing kind '%s' for module %s channel %s.",
                spec.kind,
                spec.address,
                spec.channel,
            )

    async_add_entities(entities)
    _LOGGER.debug("Added %d Nikobus switch entities.", len(entities))


def _register_nikobus_module_device(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    module_address: str,
    module_name: str,
    module_model: str,
) -> None:
    """Register a single Nikobus module as a child device in the device registry."""
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, module_address)},
        manufacturer=BRAND,
        name=module_name,
        model=module_model,
        via_device=(DOMAIN, HUB_IDENTIFIER),
    )


class NikobusSwitchCoverEntity(NikobusEntity, SwitchEntity):
    """A switch entity for cover channels routed as binary switches."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_desc: str,
        module_model: str,
    ) -> None:
        """Initialize the switch entity for a roller module."""
        super().__init__(coordinator, address, module_desc, module_model)
        self.coordinator = coordinator
        self.address = address
        self.channel = channel
        self.channel_description = channel_description

        self._attr_name = f"{module_desc} - {channel_description}"
        self._attr_unique_id = build_unique_id(
            "switch", "cover_binary", self.address, self.channel
        )

    @property
    def is_on(self) -> bool:
        """Return True if the simulated switch (cover open) is on."""
        return self.coordinator.get_cover_state(self.address, self.channel) == 0x01

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Simulate turning on the switch (opening cover)."""
        _LOGGER.debug("Turning ON (simulating open) for %s", self.channel_description)
        try:
            await self.coordinator.api.open_cover(self.address, self.channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to open cover for %s: %s",
                self.channel_description,
                err,
                exc_info=True,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Simulate turning off the switch (stopping cover)."""
        _LOGGER.debug("Turning OFF (simulating stop) for %s", self.channel_description)
        try:
            await self.coordinator.api.stop_cover(
                self.address, self.channel, direction="closing"
            )
        except NikobusError as err:
            _LOGGER.error(
                "Failed to stop cover for %s: %s",
                self.channel_description,
                err,
                exc_info=True,
            )


class NikobusSwitchEntity(NikobusEntity, SwitchEntity):
    """A switch entity representing one channel on a Nikobus module."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_name: str,
        module_model: str,
    ) -> None:
        """Initialize the switch entity."""
        super().__init__(coordinator, address, module_name, module_model)
        self._address = address
        self._channel = channel
        self._channel_description = channel_description

        self._attr_unique_id = build_unique_id(
            "switch", "relay_switch", self._address, self._channel
        )
        self._attr_name = channel_description
        self._is_on: bool | None = None

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self._is_on if self._is_on is not None else self._read_current_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle new data from the coordinator."""
        self._is_on = None
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the switch on."""
        self._is_on = True
        self.async_write_ha_state()
        try:
            await self.coordinator.api.turn_on_switch(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn on switch (module=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the switch off."""
        self._is_on = False
        self.async_write_ha_state()
        try:
            await self.coordinator.api.turn_off_switch(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to turn off switch (module=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            self._is_on = None
            self.async_write_ha_state()

    def _read_current_state(self) -> bool:
        """Fetch real-time state from the coordinator."""
        try:
            return self.coordinator.get_switch_state(self._address, self._channel)
        except NikobusError as err:
            _LOGGER.error(
                "Failed to get state for switch (module=%s, channel=%d): %s",
                self._address,
                self._channel,
                err,
            )
            return False
