"""Scene platform for the Nikobus integration."""

import logging
from typing import Any, Dict, List, Optional, Union
from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Any
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

        # Optional: feedback LED(s) that must be sent first (toggle behavior on bus)
        feedback_led_raw: Optional[Union[str, List[str]]] = scene.get("feedback_led")
        feedback_leds: List[str] = _normalize_feedback_leds(feedback_led_raw)

        _LOGGER.debug(
            "Processing scene: %s (ID: %s) | Channels: %s | FeedbackLEDs=%s",
            description,
            scene_id,
            impacted_modules_info,
            feedback_leds,
        )

        entities.append(
            NikobusSceneEntity(
                hass=hass,
                coordinator=coordinator,
                description=description,
                scene_id=scene_id,
                impacted_modules_info=impacted_modules_info,
                feedback_leds=feedback_leds,
            )
        )

    _LOGGER.debug("Adding %d Nikobus scene entities.", len(entities))
    async_add_entities(entities)


def _normalize_feedback_leds(value: Optional[Union[str, List[str]]]) -> List[str]:
    """Accept a single string or list of strings; keep any non-empty trimmed string."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


class NikobusSceneEntity(CoordinatorEntity, Scene):
    """Represents a Nikobus scene entity within Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        description: str,
        scene_id: str,
        impacted_modules_info: List[Dict[str, Any]],
        feedback_leds: Optional[List[str]] = None,
    ) -> None:
        """Initialize the scene entity with data from the Nikobus system configuration."""
        super().__init__(coordinator)
        self._hass = hass
        self._description = description
        self._scene_id = scene_id
        self._impacted_modules_info = impacted_modules_info

        # Store feedback LED addresses as-is (any non-empty string is accepted)
        self._feedback_leds: List[str] = feedback_leds or []
        if feedback_leds is None:
            _LOGGER.debug("Scene %s: no feedback_led defined.", scene_id)

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

    async def _send_feedback_leds_first(self) -> None:
        """Send each feedback LED/button address once (toggle) BEFORE enforcing final states."""
        if not self._feedback_leds:
            return

        nikobus_cmd = getattr(self.coordinator, "nikobus_command", None)
        if nikobus_cmd is None or not hasattr(nikobus_cmd, "queue_command"):
            _LOGGER.error(
                "Scene %s: coordinator has no 'nikobus_command.queue_command' to queue LED commands.",
                self._scene_id,
            )
            return

        for addr in self._feedback_leds:
            cmd = f"#N{addr}\r#E1"
            try:
                _LOGGER.debug(
                    "Scene %s: queuing feedback LED/button address first: %s",
                    self._scene_id,
                    cmd,
                )
                await nikobus_cmd.queue_command(cmd)
            except Exception as e:
                _LOGGER.error(
                    "Scene %s: failed to send feedback LED %s: %s",
                    self._scene_id,
                    addr,
                    e,
                    exc_info=True,
                )

    async def async_activate(self) -> None:
        """Activate the scene by updating only the specified channels while keeping others unchanged.
        Order: send feedback LED/button address first (might toggle a real load), then enforce final state.
        """
        _LOGGER.debug(
            "Activating scene: %s (ID: %s)", self._description, self._scene_id
        )

        # 1) Send feedback LED/button address FIRST
        await self._send_feedback_leds_first()

        module_changes: Dict[str, bytearray] = {}

        def get_value(module_type: str, state: Any) -> Optional[int]:
            """Convert a scene state value into the correct integer representation."""
            if isinstance(state, str):
                state = state.lower()

            module_state_map = {
                "switch_module": {"on": 255, "off": 0},
                "roller_module": {"open": 1, "close": 2},
            }

            if module_type in module_state_map and isinstance(state, str):
                mapped = module_state_map[module_type].get(state)
                if mapped is not None:
                    return mapped
                _LOGGER.error("Invalid state for %s: %s", module_type, state)
                return None

            if module_type == "dimmer_module":
                try:
                    return max(0, min(int(state), 255))  # Ensure valid brightness range
                except (ValueError, TypeError):
                    _LOGGER.error("Invalid state for dimmer: %s", state)
                    return None

            _LOGGER.error("Unknown module type: %s", module_type)
            return None

        # 2) Build desired channel states
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
            except (ValueError, TypeError):
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

            # Retrieve current state and update specific channel value.
            if module_id not in module_changes:
                current_state = self.coordinator.nikobus_module_states.get(
                    module_id, bytearray(12)
                )
                module_changes[module_id] = bytearray(current_state)
                _LOGGER.debug(
                    "Fetched current state for module %s: %s",
                    module_id,
                    module_changes[module_id].hex(),
                )

            # channels are 1-based in your JSON
            idx = channel - 1
            if idx < 0:
                _LOGGER.error(
                    "Invalid channel index for module %s: %s", module_id, channel
                )
                continue
            try:
                module_changes[module_id][idx] = value
            except IndexError:
                _LOGGER.error(
                    "Channel %s out of range for module %s", channel, module_id
                )
                continue

        # 3) Push module group updates to enforce final state
        for module_id, channel_states in module_changes.items():
            try:
                # Use coordinator function to get the actual number of channels.
                num_channels = self.coordinator.get_module_channel_count(module_id)
                current_state = self.coordinator.nikobus_module_states.get(
                    module_id, bytearray(12)
                )

                # Update group 1: first 6 channels (or fewer, if module has fewer channels)
                group1_channels = min(6, num_channels)
                if any(
                    channel_states[i] != current_state[i] for i in range(group1_channels)
                ):
                    hex_value = channel_states[:group1_channels].hex()
                    _LOGGER.debug(
                        "Updating group 1 for module %s with values: %s",
                        module_id,
                        hex_value,
                    )
                    self.coordinator.set_bytearray_group_state(
                        module_id, group=1, value=hex_value
                    )

                # Update group 2: if there are channels beyond the first group
                if num_channels > 6 and any(
                    channel_states[i] != current_state[i] for i in range(6, num_channels)
                ):
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
                try:
                    await self.coordinator.api.set_output_states_for_module(
                        address=module_id
                    )
                except Exception as e:
                    _LOGGER.error(
                        "Failed to set output state for module %s: %s",
                        module_id,
                        e,
                        exc_info=True,
                    )

                try:
                    await self.coordinator.async_event_handler(
                        "nikobus_refreshed", {"impacted_module_address": module_id}
                    )
                except Exception as e:
                    _LOGGER.error(
                        "Failed to handle event for module %s: %s",
                        module_id,
                        e,
                        exc_info=True,
                    )
            except Exception as e:
                _LOGGER.error(
                    "Scene %s: fatal error while applying module %s: %s",
                    self._scene_id,
                    module_id,
                    e,
                    exc_info=True,
                )
