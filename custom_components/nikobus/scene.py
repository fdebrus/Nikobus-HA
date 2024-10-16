from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import CoordinatorEntity  # Added import

from .const import DOMAIN, BRAND

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> bool:

    dataservice = hass.data[DOMAIN].get(entry.entry_id)

    entities = []

    if dataservice.api.dict_scene_data:
        for scene in dataservice.api.dict_scene_data.get("scene", []):
            impacted_modules_info = [
                {"module_id": channel.get("module_id"), "channel": channel.get("channel"), "state": channel.get("state")}
                for channel in scene.get("channels", [])
            ]

            entity = NikobusSceneEntity(
                hass,
                dataservice,
                scene.get("description"),
                scene.get("id"),
                impacted_modules_info,
            )

            entities.append(entity)

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
        return self._scene_id

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this scene."""
        # Use the scene_id as the unique identifier
        return f"nikobus_scene_{self._scene_id}"

    async def async_activate(self) -> None:
        """Activate the scene by updating only the specified channels and keeping other channels unchanged."""
        # Dictionary to hold module changes, grouping changes by module_id
        module_changes = {}

        # Group changes by module
        for module in self.impacted_modules_info:
            module_id = module.get("module_id")
            channel = module.get("channel")
            state = module.get("state")

            # Translate the state to the appropriate value (e.g., on = 0xFF, off = 0x00)
            value = 0xFF if state == "on" else 0x00

            # If we haven't fetched the module's current state yet, do so
            if module_id not in module_changes:
                # Initialize with the current state of the module from memory
                module_changes[module_id] = bytearray(self._dataservice.api._nikobus_module_states.get(module_id, bytearray(12)))

            # Update only the specified channel with the new value
            module_changes[module_id][channel - 1] = value

        # Send the combined command for each module, after updating only the specified channels
        for module_id, channel_states in module_changes.items():
            # Update the in-memory state using set_bytearray_group_state if applicable
            if len(channel_states) <= 6:
                self._dataservice.api.set_bytearray_group_state(module_id, group=1, value=channel_states[:6].hex())
            else:
                self._dataservice.api.set_bytearray_group_state(module_id, group=2, value=channel_states[6:12].hex())

            # Finally, send the updated state to the module
            await self._dataservice.api.set_output_states_for_module(module_id, channel_states)