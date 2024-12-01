import logging
from homeassistant.components.light import (
    LightEntity,
    ATTR_BRIGHTNESS,
    ColorMode,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, BRAND
from .exceptions import NikobusError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities) -> bool:
    """Set up Nikobus light entities from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    dimmer_modules = coordinator.dict_module_data.get("dimmer_module", {})

    entities = [
        NikobusLightEntity(
            hass,
            coordinator,
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
    """Represents a Nikobus light (dimmer) entity within Home Assistant."""

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
        """Initialize the light entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._description = description
        self._model = model
        self._address = address
        self._channel = channel

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS

        # Internal state variables for optimistic updates
        self._is_on = None
        self._brightness = None

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
    def is_on(self):
        """Return True if the light is on."""
        if self._is_on is not None:
            return self._is_on
        brightness = self.brightness
        return brightness > 0

    @property
    def brightness(self):
        """Return the brightness of the light."""
        if self._brightness is not None:
            return self._brightness
        try:
            return self._coordinator.get_light_brightness(self._address, self._channel)
        except NikobusError as e:
            _LOGGER.error(
                f"Failed to get brightness for light at address {self._address}, channel {self._channel}: {e}"
            )
            return 0  # Assume brightness is 0 if an error occurs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Reset the optimistic state since we have new data from the coordinator
        self._is_on = None
        self._brightness = None
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the light on with the given brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)

        # Optimistically update the internal state
        self._is_on = True
        self._brightness = brightness
        self.async_write_ha_state()

        try:
            await self._coordinator.api.turn_on_light(
                self._address, self._channel, brightness
            )
        except NikobusError as e:
            _LOGGER.error(
                f"Failed to turn on light at address {self._address}, channel {self._channel}: {e}"
            )
            # Revert the optimistic state on failure
            self._is_on = None
            self._brightness = None
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the light off."""

        # Optimistically update the internal state
        self._is_on = False
        self._brightness = 0
        self.async_write_ha_state()

        try:
            await self._coordinator.api.turn_off_light(self._address, self._channel)
        except NikobusError as e:
            _LOGGER.error(
                f"Failed to turn off light at address {self._address}, channel {self._channel}: {e}"
            )
            # Revert the optimistic state on failure
            self._is_on = None
            self._brightness = None
            self.async_write_ha_state()
