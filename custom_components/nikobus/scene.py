import logging
from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
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
        self._impacted_modules_info = impacted_modules_info

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

        def get_value(module_type, state):
            state = state.lower()
    
            module_state_map = {
                "switch": {"on": 255, "off": 0},
                "cover": {"open": 1, "close": 2}
            }
    
            if module_type == "switch" or module_type == "cover":
                if state in module_state_map[module_type]:
                    return module_state_map[module_type][state]
                else:
                    _LOGGER.error(f"Invalid state for {module_type}: {state}")
                    return None
            elif module_type == "dimmer":
                if state.isdigit():
                    return min(int(state), 255)  # Cap value for dimmers at 255
                else:
                    _LOGGER.error(f"Invalid state for dimmer: {state}")
                    return None
            else:
                _LOGGER.error(f"Unknown module type: {module_type}")
                return None

        # Group changes by module
        for module in self._impacted_modules_info:
            module_id = module.get("module_id")
            channel = int(module.get("channel"))
            state = module.get("state")

            _LOGGER.debug(f"Processing module: {module_id}, channel: {channel}, state: {state}")

            # Get the module type and determine the value
            module_type = self._dataservice.api.get_module_type(module_id)
            _LOGGER.debug(f"Detected module type for module {module_id}: {module_type}")

            value = get_value(module_type, state)
            if value is None:
                continue

            # Initialize module changes if not yet fetched
            if module_id not in module_changes:
                module_changes[module_id] = bytearray(self._dataservice.api._nikobus_module_states.get(module_id, bytearray(12)))
                _LOGGER.debug(f"Fetched current state for module {module_id}: {module_changes[module_id].hex()}")

            # Update the specific channel with the new value
            module_changes[module_id][channel - 1] = int(value)

        # Send the combined command for each module after updating only the specified channels
        for module_id, channel_states in module_changes.items():
            current_state = self._dataservice.api._nikobus_module_states.get(module_id, bytearray(12))

            # Check if any channel in group 1 (channels 1-6) was updated
            group1_updated = any(module_changes[module_id][i] != current_state[i] for i in range(6))
            # Update groups if necessary
            if group1_updated:
                hex_value = module_changes[module_id][:6].hex()
                _LOGGER.debug(f"Updating group 1 for module {module_id} with values: {hex_value}")
                self._dataservice.api.set_bytearray_group_state(module_id, group=1, value=hex_value)

            if module_type != "cover":
                # Check if any channel in group 2 (channels 7-12) was updated
                group2_updated = any(module_changes[module_id][i] != current_state[i] for i in range(6, 12))
                if group2_updated:
                    hex_value = module_changes[module_id][6:12].hex()
                    _LOGGER.debug(f"Updating group 2 for module {module_id} with values: {hex_value}")
                    self._dataservice.api.set_bytearray_group_state(module_id, group=2, value=hex_value)

            # Log the final updated state of the module and send the changes
            _LOGGER.debug(f"Sending updated state to module {module_id}: {module_changes[module_id].hex()}")
            await self._dataservice.api.set_output_states_for_module(module_id, module_changes[module_id])

            await self._dataservice.api._async_event_handler("nikobus_refreshed", {
                    'impacted_module_address': module_id
                })
