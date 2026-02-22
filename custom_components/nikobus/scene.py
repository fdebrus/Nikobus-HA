"""Scene platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

# Nikobus State Constants
STATE_STOPPED = 0x00
STATE_OPEN = 0x01
STATE_CLOSE = 0x02
STATE_ON = 0xFF
STATE_OFF = 0x00

_STATE_MAPPING = {
    "switch_module": {"on": STATE_ON, "off": STATE_OFF, "true": STATE_ON, "false": STATE_OFF},
    "roller_module": {"open": STATE_OPEN, "close": STATE_CLOSE, "stop": STATE_STOPPED}
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nikobus scenes from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[NikobusSceneEntity] = []

    if not coordinator.dict_scene_data:
        return

    scenes = coordinator.dict_scene_data.get("scene", [])

    for scene in scenes:
        scene_id = scene.get("id")
        if not scene_id:
            _LOGGER.warning("Skipping Nikobus scene with missing ID")
            continue

        entities.append(
            NikobusSceneEntity(
                coordinator=coordinator,
                scene_config=scene,
            )
        )

    async_add_entities(entities)


class NikobusSceneEntity(NikobusEntity, Scene):
    """Representation of a Nikobus scene."""

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        scene_config: dict[str, Any],
    ) -> None:
        """Initialize the Nikobus scene."""
        scene_id = scene_config["id"]
        description = scene_config.get("description", f"Scene {scene_id}")
        
        super().__init__(
            coordinator=coordinator,
            address=scene_id,
            name=description,
            model="Software Scene",
        )
        self._scene_id = scene_id
        self._attr_name = description
        self._attr_unique_id = f"nikobus_scene_{scene_id}"
        
        self._channels = scene_config.get("channels", [])
        self._feedback_leds = self._normalize_feedback_leds(scene_config.get("feedback_led"))
        
        # Guard against overlapping roller release tasks
        self._module_tokens: dict[str, str] = {}

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the scene by setting multiple module channels."""
        _LOGGER.info("Activating Nikobus scene: %s", self.name)

        # 1. Handle Feedback LEDs (Toggles) first if defined
        await self._process_feedback_leds()

        module_updates: dict[str, bytearray] = {}
        roller_tasks: dict[str, dict[str, Any]] = {}

        # 2. Build the state map for all impacted modules
        for channel_info in self._channels:
            module_id = channel_info.get("module_id")
            chan_num = channel_info.get("channel")
            state = channel_info.get("state")

            if not module_id or chan_num is None:
                continue

            module_type = self.coordinator.get_module_type(module_id)
            byte_val = self._state_to_byte(module_type, state)

            if byte_val is None:
                continue

            if module_id not in module_updates:
                # Start with current known state from coordinator
                current = self.coordinator.nikobus_module_states.get(module_id, bytearray(12))
                module_updates[module_id] = bytearray(current)

            # Update specific channel (0-indexed)
            idx = int(chan_num) - 1
            if 0 <= idx < 12:
                module_updates[module_id][idx] = byte_val

            # Track rollers that need a timed stop
            if module_type == "roller_module" and byte_val in (STATE_OPEN, STATE_CLOSE):
                op_time = self.coordinator.get_cover_operation_time(module_id, chan_num)
                if op_time > 0:
                    module_task = roller_tasks.setdefault(module_id, {"indexes": set(), "delay": 0})
                    module_task["indexes"].add(idx)
                    module_task["delay"] = max(module_task["delay"], op_time + 3.0)

        # 3. Commit updates to hardware
        for module_id, final_state in module_updates.items():
            await self._apply_module_state(module_id, final_state)
            
            # 4. Schedule roller release if needed
            if module_id in roller_tasks:
                task_info = roller_tasks[module_id]
                token = uuid.uuid4().hex
                self._module_tokens[module_id] = token
                self.hass.async_create_task(
                    self._delayed_roller_stop(
                        module_id, final_state, task_info["indexes"], task_info["delay"], token
                    )
                )

    async def _apply_module_state(self, module_id: str, state: bytearray) -> None:
        """Push the bytearray state to the Nikobus module via coordinator."""
        num_chans = self.coordinator.get_module_channel_count(module_id)
        
        # Update Group 1 (1-6)
        self.coordinator.set_bytearray_group_state(module_id, 1, state[:6].hex())
        
        # Update Group 2 (7-12) if applicable
        if num_chans > 6:
            self.coordinator.set_bytearray_group_state(module_id, 2, state[6:num_chans].hex())

        await self.coordinator.api.set_output_states_for_module(address=module_id)
        
        # Notify coordinator of manual refresh
        await self.coordinator.async_event_handler(
            "nikobus_refreshed", {"impacted_module_address": module_id}
        )

    async def _delayed_roller_stop(
        self, module_id: str, state: bytearray, indexes: set[int], delay: float, token: str
    ) -> None:
        """Stop roller movement after operation time expires."""
        await asyncio.sleep(delay)

        # Abort if a newer scene activation has taken control of this module
        if self._module_tokens.get(module_id) != token:
            return

        stop_state = bytearray(state)
        for idx in indexes:
            stop_state[idx] = STATE_STOPPED
            
        _LOGGER.debug("Timed stop for rollers on module %s", module_id)
        await self._apply_module_state(module_id, stop_state)

    def _state_to_byte(self, module_type: str | None, state: Any) -> int | None:
        """Convert friendly state strings/values to Nikobus bytes."""
        if module_type == "dimmer_module":
            try:
                return max(0, min(int(state), 255))
            except (ValueError, TypeError):
                return None

        clean_state = str(state).lower()
        return _STATE_MAPPING.get(module_type or "", {}).get(clean_state)

    def _normalize_feedback_leds(self, value: Any) -> list[str]:
        """Ensure feedback LEDs are a list of cleaned strings."""
        if not value:
            return []
        if isinstance(value, str):
            return [value.strip()]
        return [str(v).strip() for v in value if v]

    async def _process_feedback_leds(self) -> None:
        """Send feedback LED trigger commands."""
        for addr in self._feedback_leds:
            _LOGGER.debug("Triggering feedback LED/Button: %s", addr)
            # Standard Nikobus command for button trigger
            cmd = f"#N{addr}\r#E1"
            await self.coordinator.nikobus_command.queue_command(cmd)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Stateless scene: Ignore general coordinator updates."""
        pass