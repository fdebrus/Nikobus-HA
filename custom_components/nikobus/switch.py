import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    """Set up Nikobus switch entities from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    switch_modules = coordinator.dict_module_data.get("switch_module", {})

    entities = [
        NikobusSwitchEntity(
            hass,
            coordinator,
            switch_module_data.get("description"),
            switch_module_data.get("model"),
            address,
            i,
            channel["description"],
        )
        for address, switch_module_data in switch_modules.items()
        for i, channel in enumerate(switch_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)


class NikobusSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Represents a Nikobus switch entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        description,
        model,
        address,
        channel,
        channel_description,
    ) -> None:
        """Initialize the switch entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"

    @property
    def device_info(self):
        """Return device information about this switch."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def is_on(self):
        """Return True if the switch is on."""
        return self._coordinator.get_switch_state(self._address, self._channel)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the switch on."""
        try:
            await self._coordinator.api.turn_on_switch(self._address, self._channel)
        except Exception as e:
            _LOGGER.error(
                f"Failed to turn on switch at address {self._address}, channel {self._channel}: {e}"
            )
        self.async_write_ha_state()

    async def async_turn_off(self):
        """Turn the switch off."""
        try:
            await self._coordinator.api.turn_off_switch(self._address, self._channel)
        except Exception as e:
            _LOGGER.error(
                f"Failed to turn off switch at address {self._address}, channel {self._channel}: {e}"
            )
        self.async_write_ha_state()
