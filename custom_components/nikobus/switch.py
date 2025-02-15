"""Switch platform for the Nikobus integration with module-level devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator
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
    entities: list = []

    # Process standard switch_module entities
    switch_modules: dict[str, Any] = coordinator.dict_module_data.get("switch_module", {})
    for address, switch_module_data in switch_modules.items():
        module_desc = switch_module_data.get("description", f"Module {address}")
        model = switch_module_data.get("model", "Unknown Module Model")

        _register_nikobus_module_device(
            device_registry=device_registry,
            entry=entry,
            module_address=address,
            module_name=module_desc,
            module_model=model,
        )

        for channel_index, channel_info in enumerate(switch_module_data.get("channels", []), start=1):
            if channel_info["description"].startswith("not_in_use"):
                continue

            entities.append(
                NikobusSwitchEntity(
                    coordinator=coordinator,
                    address=address,
                    channel=channel_index,
                    channel_description=channel_info["description"],
                    module_name=module_desc,
                    module_model=model,
                )
            )

    # Process roller_module channels marked with use_as_switch
    # (These were stored by cover.py during its setup.)
    roller_switch_data = hass.data.setdefault(DOMAIN, {}).get("switch_entities", [])
    for switch_data in roller_switch_data:
        entities.append(
            NikobusSwitchCoverEntity(
                coordinator=switch_data["coordinator"],
                address=switch_data["address"],
                channel=switch_data["channel"],
                channel_description=switch_data["channel_description"],
                module_desc=switch_data["module_desc"],
                module_model=switch_data["module_model"],
            )
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

class NikobusSwitchCoverEntity(SwitchEntity):
    """A switch entity for roller modules using `use_as_switch`."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_desc: str,
        module_model: str,
    ):
        """Initialize the entity as a switch inside the roller module."""
        super().__init__()
        self.coordinator = coordinator
        self.address = address
        self.channel = channel
        self.channel_description = channel_description
        self.module_desc = module_desc
        self.module_model = module_model

        # Set basic attributes
        self._attr_name = f"{module_desc} - {channel_description}"
        self._attr_unique_id = f"{DOMAIN}_switch_{self.address}_{self.channel}"

    @property
    def device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self.address)},  # Same as covers
            "manufacturer": BRAND,
            "name": self.module_desc,
            "model": self.module_model,
        }

    @property
    def is_on(self):
        """Return whether the cover is open (simulated as switch ON)."""
        return self.coordinator.get_cover_state(self.address, self.channel) == 0x01

    async def async_turn_on(self, **kwargs):
        """Simulate turning on the switch (opening cover)."""
        _LOGGER.debug("Turning ON (simulating open) for %s", self.channel_description)
        await self.coordinator.api.open_cover(self.address, self.channel)

    async def async_turn_off(self, **kwargs):
        """Simulate turning off the switch (closing cover)."""
        _LOGGER.debug("Turning OFF (simulating stop) for %s", self.channel_description)
        await self.coordinator.api.stop_cover(self.address, self.channel, direction="closing")

class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
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
        super().__init__(coordinator)
        self._address = address
        self._channel = channel
        self._channel_description = channel_description
        self._module_name = module_name
        self._module_model = module_model

        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_name = channel_description
        self._is_on: bool | None = None

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info referencing the module."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "manufacturer": BRAND,
            "name": self._module_name,
            "model": self._module_model,
        }

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
