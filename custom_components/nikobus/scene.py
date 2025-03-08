"""Scene platform for the Nikobus integration."""

import logging

from typing import Any, Dict, List, Optional
from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Nikobus scenes from a config entry."""
    _LOGGER.debug("Setting up Nikobus scenes.")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: List[NikobusSceneEntity] = []

    scene_data = (
        coordinator.dict_scene_data.get("scene", [])
        if coordinator.dict_scene_data
        else []
    )

    for scene in scene_data:
        scene_id = scene.get("id")
        description = scene.get("description", f"Unnamed Scene {scene_id}")

        if not scene_id:
            _LOGGER.warning("Skipping scene with missing ID: %s", scene)
            continue

        impacted_modules_info = [
            {
                "module_id": channel.get("module_id", "unknown"),
                "channel": channel.get("channel", -1),
                "state": channel.get("state", 0),
            }
            for channel in scene.get("channels", [])
        ]

        _LOGGER.debug(
            "Processing scene: %s (ID: %s) | Channels: %s",
            description,
            scene_id,
            impacted_modules_info,
        )

        entities.append(
            NikobusSceneEntity(
                hass, coordinator, description, scene_id, impacted_modules_info
            )
        )

    _LOGGER.debug("Adding %d Nikobus scene entities.", len(entities))
    async_add_entities(entities)


class NikobusSceneEntity(CoordinatorEntity, Scene):
    """Represents a Nikobus scene entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        description: str,
        scene_id: str,
        impacted_modules_info: List[Dict[str, Any]],
    ) -> None:
        """Initialize the scene entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._hass = hass
        self._description = description
        self._scene_id = scene_id
        self._impacted_modules_info = impacted_modules_info

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this scene."""
        return f"{DOMAIN}_scene_{self._scene_id}"

    @property
    def device_info(self) -> Dict[str, Any]:
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
        """Activate the scene by updating only the specified channels while keeping others unchanged."""
        _LOGGER.debug(
            "Activating scene: %s (ID: %s)", self._description, self._scene_id
        )

        module_changes = {}

        def get_value(module_type: str, state: Any) -> Optional[int]:
            """Convert a scene state value into the correct integer representation."""
            if isinstance(state, str):
                state = state.lower()

            module_state_map = {
                "switch_module": {"on": 255, "off": 0},
                "roller_module": {"open": 1, "close": 2},
            }

            if module_type in module_state_map and isinstance(state, str):
                if state in module_state_map[module_type]:
                    return module_state_map[module_type][state]
                _LOGGER.error("Invalid state for %s: %s", module_type, state)
                return None

            if module_type == "dimmer_module":
                try:
                    return max(0, min(int(state), 255))  # Ensure valid brightness range
                except ValueError:
                    _LOGGER.error("Invalid state for dimmer: %s", state)
                    return None

            _LOGGER.error("Unknown module type: %s", module_type)
            return None

        # Group changes by module
        for module in self._impacted_modules_info:
            module_id = module.get("module_id")
            channel = module.get("channel")
            state = module.get("state")

            if not module_id or channel is None:
                _LOGGER.warning(
                    "Skipping module with missing ID or channel: %s", module
                )
                continue

            try:
                channel = int(channel)
            except ValueError:
                _LOGGER.error(
                    "Invalid channel number for module %s: %s", module_id, channel
                )
                continue

            _LOGGER.debug(
                "Processing module: %s, channel: %s, state: %s",
                module_id,
                channel,
                state,
            )

            module_type = self.coordinator.get_module_type(module_id) or "unknown"
            _LOGGER.debug(
                "Detected module type for module %s: %s", module_id, module_type
            )

            value = get_value(module_type, state)
            if value is None:
                continue

            # Initialize module changes if not yet fetched
            if module_id not in module_changes:
                module_changes[module_id] = bytearray(
                    self.coordinator.nikobus_module_states.get(module_id, bytearray(12))
                )
                _LOGGER.debug(
                    "Fetched current state for module %s: %s",
                    module_id,
                    module_changes[module_id].hex(),
                )

            # Update the specific channel with the new value
            module_changes[module_id][channel - 1] = value

        # Send the combined command for each module after updating only the specified channels
        for module_id, channel_states in module_changes.items():
            current_state = self.coordinator.nikobus_module_states.get(
                module_id, bytearray(12)
            )
            num_channels = len(
                current_state
            )  # Use the actual number of channels for this module

            module_type = self.coordinator.get_module_type(module_id)

            # Group 1: first 6 channels (or less if the module has fewer channels)
            group1_channels = min(6, num_channels)
            group1_updated = any(
                channel_states[i] != current_state[i] for i in range(group1_channels)
            )

            if group1_updated:
                hex_value = channel_states[:group1_channels].hex()
                _LOGGER.debug(
                    "Updating group 1 for module %s with values: %s",
                    module_id,
                    hex_value,
                )
                self.coordinator.set_bytearray_group_state(
                    module_id, group=1, value=hex_value
                )

            # Group 2: if there are channels beyond the first group
            if num_channels > 6:
                group2_updated = any(
                    channel_states[i] != current_state[i]
                    for i in range(6, num_channels)
                )
                if group2_updated:
                    hex_value = channel_states[6:num_channels].hex()
                    _LOGGER.debug(
                        "Updating group 2 for module %s with values: %s",
                        module_id,
                        hex_value,
                    )
                    self.coordinator.set_bytearray_group_state(
                        module_id, group=2, value=hex_value
                    )

            _LOGGER.debug(
                "Sending updated state to module %s: %s", module_id, channel_states
            )
            await self.coordinator.api.set_output_states_for_module(address=module_id)

            # Notify listeners of the state change
            await self.coordinator.async_event_handler(
                "nikobus_refreshed", {"impacted_module_address": module_id}
            )