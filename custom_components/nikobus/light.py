import logging
from homeassistant.components.light import LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)

DEFAULT_BRIGHTNESS = 255


async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    dimmer_modules = dataservice.api.dict_module_data.get("dimmer_module", {})

    entities = [
        NikobusLightEntity(
            hass,
            dataservice,
            dimmer_module_data.get("description"),
            dimmer_module_data.get("model"),
            address,
            i,
            channel["description"],
        )
        for address, dimmer_module_data in dimmer_modules.items()
        for i, channel in enumerate(dimmer_module_data.get("channels", []), start=1)
        if not channel["description"].startswith("not_in_use")
    ]

    async_add_entities(entities)


class NikobusLightEntity(CoordinatorEntity, LightEntity):
    """Represents a Nikobus light entity within Home Assistant."""

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
        """Initialize the light entity with data from the Nikobus system configuration."""
        super().__init__(dataservice)
        self._dataservice = dataservice
        self._state = None
        self._brightness = None
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"

    @property
    def device_info(self):
        """Return device information about this light."""
        return {
            "identifiers": {(DOMAIN, self._address)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": self._model,
        }

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def color_mode(self):
        """Return the color mode of the light."""
        return "brightness"

    @property
    def supported_color_modes(self):
        """Return the supported color modes."""
        return {"brightness"}

    @property
    def is_on(self):
        """Return True if the light is on."""
        return self._state is True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._state = bool(
            self._dataservice.api.get_light_state(self._address, self._channel)
        )
        self._brightness = self._dataservice.api.get_light_brightness(
            self._address, self._channel
        )
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn on the light."""
        # Get the desired brightness
        self._brightness = kwargs.get("brightness", DEFAULT_BRIGHTNESS)

        try:
            # Send the turn on command without updating the state optimistically
            await self._dataservice.api.turn_on_light(
                self._address,
                self._channel,
                self._brightness,
                completion_handler=self._on_light_turned_on,
            )
        except Exception as e:
            _LOGGER.error(
                f"Failed to send turn on command for light at address {self._address}, channel {self._channel}: {e}"
            )

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        try:
            # Send the turn off command without updating the state optimistically
            await self._dataservice.api.turn_off_light(
                self._address,
                self._channel,
                completion_handler=self._on_light_turned_off,
            )
        except Exception as e:
            _LOGGER.error(
                f"Failed to send turn off command for light at address {self._address}, channel {self._channel}: {e}"
            )

    async def _on_light_turned_on(self, success):
        """Handler called when the light has been processed."""
        if success:
            # Update the state and brightness
            await self._dataservice.api.set_bytearray_state(self._address, self._channel, self._brightness)
            self._state = True
            _LOGGER.debug(
                f"Successfully turned on light at {self._address}, channel {self._channel}, brightness {self._brightness}"
            )
            # Update the UI
            self.async_write_ha_state()
        else:
            _LOGGER.error(
                f"Turn on command failed for light at {self._address}, channel {self._channel}, brightness {self._brightness}"
            )

    async def _on_light_turned_off(self, success):
        """Handler called when the light has been processed."""
        if success:
            # Update the state and brightness
            await self._dataservice.api.set_bytearray_state(self._address, self._channel, 0x00)
            self._state = False
            self._brightness = 0
            _LOGGER.debug(
                f"Successfully turned off light at {self._address}, channel {self._channel}, brightness {self._brightness}"
            )
            # Update the UI
            self.async_write_ha_state()
        else:
            _LOGGER.error(
                f"Turn off command failed for light at {self._address}, channel {self._channel}, brightness {self._brightness}"
            )
