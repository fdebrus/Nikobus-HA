import logging
from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND

_LOGGER = logging.getLogger(__name__)  # Initialize logger

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:
    _LOGGER.debug("Setting up Nikobus scenes.")

    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = []

    if dataservice.api.dict_scene_data:
        for scene in dataservice.api.dict_scene_data.get("scene", []):
            _LOGGER.debug(f"Processing scene: {scene.get('description')} (ID: {scene.get('id')})")
            impacted_modules_info = [
                {"module_id": channel.get("module_id"), "channel": channel.get("channel"), "state": channel.get("state")}
                for channel in scene.get("channels", [])
            ]
            _LOGGER.debug(f"Scene channels: {impacted_modules_info}")

            entity = NikobusSceneEntity(
                hass,
                dataservice,
                scene.get("description"),
                scene.get("id"),
                impacted_modules_info,
            )

            entities.append(entity)

        _LOGGER.debug(f"Adding {len(entities)} Nikobus scene entities.")
        async_add_entities(entities)

class NikobusSceneEntity(CoordinatorEntity, Scene):
    def __init__(self, hass: HomeAssistant, dataservice, description, scene_id, impacted_modules_info) -> None:
        super().__init__(dataservice)
        self._hass = hass
        self._dataservice = dataservice
        self._description = description
        self._scene_id = scene_id
        self.impacted_modules_info = impacted_modules_info

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this scene."""
        return f"nikobus_scene_{self._scene_id}"

    @property
    def device_info(self):
        """Return device information to link this scene to the Nikobus integration."""
        return {
            "identifiers": {(DOMAIN, self._scene_id)},
            "name": self._description,
            "manufacturer": BRAND,
            "model": "Scene",
        }

    @property
    def name(self) -> str:
        """Return the name of the scene."""
        return self._description

    async def async_activate(self) -> None:
        """Activate the scene by updating only the specified channels and keeping other channels unchanged."""
        _LOGGER.debug(f"Activating scene: {self._description} (ID: {self._scene_id})")
        module_changes = {}

        # Group changes by module
        for module in self.impacted_modules_info:
            module_id = module.get("module_id")
            channel = int(module.get("channel"))
            state = module.get("state")

            _LOGGER.debug(f"Processing module: {module_id}, channel: {channel}, state: {state}")

            # Get the type of module (dimmer, switch, or cover)
            module_type = self._dataservice.api.get_module_type(module_id)
            _LOGGER.debug(f"Detected module type for module {module_id}: {module_type}")

            # Handle state values based on module type (dimmer, shutter, or switch)
            if module_type == "switch":
                if state.lower() == "on":
                    value = 255  # Full power for switch (0xFF)
                    _LOGGER.debug(f"Switch ON: Setting value 0xFF for module {module_id}, channel {channel}")
                elif state.lower() == "off":
                    value = 0  # Power off for switch
                    _LOGGER.debug(f"Switch OFF: Setting value 0x00 for module {module_id}, channel {channel}")
                else:
                    _LOGGER.error(f"Invalid state for switch: {state} for module {module_id}, channel {channel}")
                    continue
            elif module_type == "dimmer":
                if state.isdigit():
                    value = int(state)  # Numeric state for dimmers
                    if value > 255:
                        value = 255  # Cap value for dimmers
                    _LOGGER.debug(f"Setting dimmer value {value} for module {module_id}, channel {channel}")
                else:
                    _LOGGER.error(f"Invalid state for dimmer: {state} for module {module_id}, channel {channel}")
                    continue
            elif module_type == "cover":
                if state.isdigit():
                    value = int(state)  # Numeric state for covers (0-100 for shutters)
                    if value > 100:
                        value = 100  # Cap value for covers (100% fully open)
                    _LOGGER.debug(f"Setting cover value {value} for module {module_id}, channel {channel}")
                else:
                    _LOGGER.error(f"Invalid state for cover: {state} for module {module_id}, channel {channel}")
                    continue

            else:
                _LOGGER.error(f"Unknown module type: {module_type} for module {module_id}, channel {channel}")
                continue

            # Initialize module changes if not yet fetched
            if module_id not in module_changes:
                module_changes[module_id] = bytearray(self._dataservice.api._nikobus_module_states.get(module_id, bytearray(12)))
                _LOGGER.debug(f"Fetched current state for module {module_id}: {module_changes[module_id].hex()}")

            # Update the specific channel with the new value
            module_changes[module_id][channel - 1] = int(value)

        # Send the combined command for each module after updating only the specified channels
        for module_id, channel_states in module_changes.items():
            group1_updated = False
            group2_updated = False

            # Check if any channel in group 1 (channels 1-6) was updated
            for i in range(6):
                if module_changes[module_id][i] != self._dataservice.api._nikobus_module_states.get(module_id, bytearray(12))[i]:
                    group1_updated = True
                    break

            # Check if any channel in group 2 (channels 7-12) was updated
            for i in range(6, 12):
                if module_changes[module_id][i] != self._dataservice.api._nikobus_module_states.get(module_id, bytearray(12))[i]:
                    group2_updated = True
                    break

            # Update group 1 (channels 1-6) if there was a change
            if group1_updated:
                hex_value = module_changes[module_id][:6].hex()
                _LOGGER.debug(f"Updating group 1 for module {module_id} with values: {hex_value}")
                self._dataservice.api.set_bytearray_group_state(module_id, group=1, value=hex_value)

            # Update group 2 (channels 7-12) if there was a change
            if group2_updated:
                hex_value = module_changes[module_id][6:12].hex()
                _LOGGER.debug(f"Updating group 2 for module {module_id} with values: {hex_value}")
                self._dataservice.api.set_bytearray_group_state(module_id, group=2, value=hex_value)

            # Finally, send the updated state to the module
            _LOGGER.debug(f"Sending updated state to module {module_id}: {module_changes[module_id]}")
            await self._dataservice.api.set_output_states_for_module(module_id, module_changes[module_id])
