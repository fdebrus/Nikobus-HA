import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    """Set up Nikobus switch entities from a config entry."""
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    switch_modules = dataservice.api.dict_module_data.get("switch_module", {})

    entities = [
        NikobusSwitchEntity(
            hass,
            dataservice,
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
        dataservice,
        description,
        model,
        address,
        channel,
        channel_description,
    ) -> None:
        """Initialize the switch entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._state = None
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
        return self._state is True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._state = bool(
            self._dataservice.api.get_switch_state(self._address, self._channel)
        )
        self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the switch on."""
        try:
            # Send the turn on command without optimistic state update
            await self._dataservice.api.turn_on_switch(
                self._address,
                self._channel,
                completion_handler=self._on_switch_turned_on,
            )
        except Exception as e:
            _LOGGER.error(
                f"Failed to send turn on command for switch at address {self._address}, channel {self._channel}: {e}"
            )

    async def async_turn_off(self):
        """Turn the switch off."""
        try:
            # Send the turn off command without optimistic state update
            await self._dataservice.api.turn_off_switch(
                self._address,
                self._channel,
                completion_handler=self._on_switch_turned_off,
            )
        except Exception as e:
            _LOGGER.error(
                f"Failed to send turn off command for switch at address {self._address}, channel {self._channel}: {e}"
            )

    async def _on_switch_turned_on(self, success):
        """Handler called when the switch command has been processed."""
        if success:
            # Update the state only if the command succeeded
            await self._dataservice.api.set_bytearray_state(self._address, self._channel, 0xFF)
            self._state = True
            _LOGGER.debug(
                f"Successfully turned on switch at {self._address}, channel {self._channel}"
            )
            # Update the UI
            self.async_write_ha_state()
        else:
            _LOGGER.error(
                f"Turn on command failed for switch at {self._address}, channel {self._channel}"
            )

    async def _on_switch_turned_off(self, success):
        """Handler called when the switch command has been processed."""
        if success:
            # Update the state only if the command succeeded
            await self._dataservice.api.set_bytearray_state(self._address, self._channel, 0x00)
            self._state = False
            _LOGGER.debug(
                f"Successfully turned off switch at {self._address}, channel {self._channel}"
            )
            # Update the UI
            self.async_write_ha_state()
        else:
            _LOGGER.error(
                f"Turn off command failed for switch at {self._address}, channel {self._channel}"
            )
