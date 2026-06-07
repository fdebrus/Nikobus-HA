"""Scene platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .button import op_point_display_name
from .const import (
    CATEGORY_SCENES,
    DOMAIN,
    EVENT_SCENE_ACTIVATED,
    press_signal,
)
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

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
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus scenes from a config entry.

    Two sources contribute scene entities:

    1. **User-authored scenes** from ``nikobus_scene_config.json``
       (legacy software-scene path: HA-side fan-out over individual
       module channels). One :class:`NikobusSceneEntity` per entry.
    2. **CF activation broadcasts** classified by the library during
       discovery (``38 41 XX`` / ``38 80 XX`` addresses). One
       :class:`NikobusCFSceneEntity` per entry — activation = single
       bus-frame broadcast, output modules fire atomically via their
       existing link records.
    """
    coordinator: NikobusDataCoordinator = entry.runtime_data
    entities: list[Scene] = []

    if coordinator.dict_scene_data:
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

    cf_data = coordinator.cf_storage.data.get("nikobus_cf", {}) if coordinator.cf_storage else {}
    for bus_address, cf in cf_data.items():
        if not isinstance(cf, dict):
            continue
        entities.append(
            NikobusCFSceneEntity(
                coordinator=coordinator,
                bus_address=bus_address,
                cf_config=cf,
            )
        )

    async_add_entities(entities)


class NikobusCFSceneEntity(NikobusEntity, Scene):
    """A Central Function activation broadcast surfaced as an HA scene.

    Activation sends a single bus frame (``#N<bus_address>\\r#E1``) —
    the same shape a wall button or remote produces. Output modules
    with link records pointing to ``bus_address`` fire their linked
    actions in unison; no HA-side per-channel fan-out needed.

    Pattern + member list are surfaced as entity attributes so a user
    (or a future typed-mapping pass) can identify roller-pair vs
    switch-pair CFs without reaching into storage.
    """

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        bus_address: str,
        cf_config: dict[str, Any],
    ) -> None:
        addr = str(bus_address).upper()
        pattern = str(cf_config.get("pattern") or "unknown")
        outputs = cf_config.get("outputs") or []
        member_count = len(outputs) if isinstance(outputs, list) else 0

        # A name imported from the .nkb (nkb-sourced scenes — shutter /
        # master groups that carry their real project name) wins outright.
        # Otherwise build a default from what we know on the bus; the user
        # can still rename via the standard HA flow.
        imported = cf_config.get("name")
        if isinstance(imported, str) and imported.strip():
            name = imported.strip()
        elif pattern == "switch_pair":
            name = f"Nikobus switch CF {addr} ({member_count} ch)"
        elif pattern == "roller_pair":
            name = f"Nikobus roller CF {addr} ({member_count} ch)"
        elif pattern in ("light_scene", "nkb_scene"):
            # CF triggered by a real wall button / IR input. ``addr`` is
            # the trigger's bus address.
            name = f"Nikobus scene {addr} ({member_count} ch)"
        else:
            name = f"Nikobus CF {addr} ({member_count} ch)"

        super().__init__(
            coordinator=coordinator,
            address=addr,
            name=name,
            model=f"CF Broadcast ({pattern})",
            via_device=(DOMAIN, CATEGORY_SCENES),
        )
        self._bus_address = addr
        self._pattern = pattern
        self._outputs = outputs if isinstance(outputs, list) else []
        # Every address that fires this scene (one CF, many triggers).
        # Falls back to the canonical address for older stored records.
        triggers = cf_config.get("triggered_by")
        if isinstance(triggers, list) and triggers:
            self._triggered_by = [str(t).upper() for t in triggers]
        else:
            self._triggered_by = [addr]
        self._attr_name = name
        self._attr_unique_id = f"nikobus_cf_{addr.lower()}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parent = super().extra_state_attributes or {}
        return {
            **parent,
            "bus_address": self._bus_address,
            "pattern": self._pattern,
            "member_count": len(self._outputs),
            # Every button / IR code that fires this scene on the bus, each
            # as "Name (ADDRESS)" (one Central Function, many triggers).
            "triggered_by": self._trigger_labels(),
            # Human-readable member list: module name (address) + level.
            "outputs": self._human_outputs(),
        }

    def _trigger_labels(self) -> list[str]:
        """Display labels of every button / IR code that triggers this scene."""
        return [self._trigger_label(addr) for addr in self._triggered_by]

    def _trigger_label(self, address: str) -> str:
        """Display label of a single trigger address, as "Name (ADDRESS)"."""
        ctx = self.coordinator.get_button_context(address)
        if ctx is None:
            return address
        physical_addr, key_label, op_point, phys = ctx
        label = op_point_display_name(
            physical_addr, key_label, op_point, parent_phys=phys
        )
        return f"{label} ({address})"

    def _human_outputs(self) -> list[dict[str, Any]]:
        """Members with module names + level; address kept for reference."""
        out_list: list[dict[str, Any]] = []
        for member in self._outputs:
            if not isinstance(member, dict):
                continue
            entry: dict[str, Any] = {
                "module": self.coordinator.address_label(member.get("module_address")),
                "channel": member.get("channel"),
                "action": member.get("mode"),
            }
            level = member.get("t1")
            if level not in (None, ""):
                entry["level"] = level
            out_list.append(entry)
        return out_list

    async def async_added_to_hass(self) -> None:
        """Watch this scene's trigger addresses so a physical activation
        fires an event. One per-address signal per trigger (one Central
        Function, many triggers) wakes only this scene, not every scene."""
        await super().async_added_to_hass()
        for addr in self._triggered_by:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, press_signal(addr), self._handle_trigger
                )
            )

    @callback
    def _handle_trigger(self, data: dict) -> None:
        """Fire ``nikobus_scene_activated`` when one of this scene's trigger
        addresses is seen on the bus — physical press or HA-originated frame
        alike. Routed by address, so any delivery here is a match."""
        fired = str(data.get("address") or "").upper()
        self.hass.bus.async_fire(
            EVENT_SCENE_ACTIVATED,
            {
                "address": fired,
                "name": self._attr_name,
                "entity_id": self.entity_id,
                "member_count": len(self._outputs),
            },
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Send the CF activation broadcast on the bus."""
        await self.coordinator.async_activate_cf_broadcast(self._bus_address)


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
            via_device=(DOMAIN, CATEGORY_SCENES),
        )
        self._scene_id = scene_id
        self._attr_name = description
        self._attr_unique_id = f"nikobus_scene_{scene_id}"

        self._channels = scene_config.get("channels", [])
        self._feedback_leds = self._normalize_feedback_leds(scene_config.get("feedback_led"))

        # Guard against overlapping roller release tasks
        self._module_tokens: dict[str, str] = {}
        self._roller_stop_tasks: list[asyncio.Task[None]] = []

    async def async_added_to_hass(self) -> None:
        """Register cleanup of pending roller-stop tasks on removal."""
        await super().async_added_to_hass()

        def _cancel_roller_tasks() -> None:
            self._module_tokens.clear()
            for task in self._roller_stop_tasks:
                task.cancel()
            self._roller_stop_tasks.clear()

        self.async_on_remove(_cancel_roller_tasks)

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the scene, surfacing a bus failure as a clean error."""
        try:
            await self._activate()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _activate(self) -> None:
        """Activate the scene by setting multiple module channels."""
        _LOGGER.info("Activating Nikobus scene: %s", self.name)

        # 1. Handle Feedback LEDs (Toggles) first if defined
        await self._process_feedback_leds()

        module_updates: dict[str, bytearray] = {}
        roller_tasks: dict[str, dict[str, Any]] = {}

        # 2. Build the state map for all impacted modules
        for channel_info in self._channels:
            module_id = (channel_info.get("module_id") or "").upper()
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
            try:
                idx = int(chan_num) - 1
            except (ValueError, TypeError):
                _LOGGER.warning("Scene channel number %r is not a valid integer — skipping", chan_num)
                continue
            if 0 <= idx < len(module_updates[module_id]):
                module_updates[module_id][idx] = byte_val

            # Track rollers that need a timed stop
            if module_type == "roller_module" and byte_val in (STATE_OPEN, STATE_CLOSE):
                # Determine direction based on the state being sent
                direction = "up" if byte_val == STATE_OPEN else "down"
                op_time = self.coordinator.get_cover_operation_time(module_id, chan_num, direction=direction)

                if op_time > 0:
                    module_task = roller_tasks.setdefault(module_id, {"indexes": set(), "delay": 0})
                    module_task["indexes"].add(idx)
                    # Use the specific direction's time + buffer
                    module_task["delay"] = max(module_task["delay"], op_time + 3.0)

        # 3. Commit updates to hardware
        # All rollers in the scene share the same stop deadline: the
        # longest per-channel ``op_time + 3 s`` across every impacted
        # cover, regardless of which module it sits on. This gives each
        # cover the most generous buffer the scene asks for, which
        # absorbs bus contention when several modules are commanded
        # back to back. The previous per-module max meant a cover with
        # the longest op_time on a busy bus could see its stop fire
        # before the motor reached the end-stop — manifesting as a
        # cover that froze at e.g. 86 % when the same scene worked
        # cleanly when triggered manually on an idle bus.
        global_delay = max(
            (info["delay"] for info in roller_tasks.values()),
            default=0.0,
        )

        for module_id, final_state in module_updates.items():
            await self._apply_module_state(module_id, final_state)

            # 4. Schedule roller release if needed
            if module_id in roller_tasks:
                task_info = roller_tasks[module_id]
                token = uuid.uuid4().hex
                self._module_tokens[module_id] = token
                task = self.hass.async_create_task(
                    self._delayed_roller_stop(
                        module_id, final_state, task_info["indexes"], global_delay, token
                    )
                )
                self._roller_stop_tasks.append(task)
                task.add_done_callback(
                    lambda t: self._roller_stop_tasks.remove(t) if t in self._roller_stop_tasks else None
                )

    async def _apply_module_state(self, module_id: str, state: bytearray) -> None:
        """Push the bytearray state to the Nikobus module via coordinator."""
        num_chans = self.coordinator.get_module_channel_count(module_id)

        # Update Group 1 (1-6)
        self.coordinator.set_bytearray_group_state(module_id, 1, state[:6].hex())

        # Update Group 2 (7-12) if applicable
        if num_chans > 6:
            self.coordinator.set_bytearray_group_state(module_id, 2, state[6:12].hex())

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
        try:
            await self._apply_module_state(module_id, stop_state)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error("Failed to send timed stop for module %s: %s", module_id, err)

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
            _LOGGER.debug("Triggering feedback LED for %s", addr)
            # Standard Nikobus button trigger — sent as a repeated burst
            # (see coordinator.async_send_button_press) so it isn't
            # dropped under bus contention.
            await self.coordinator.async_send_button_press(addr)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Stateless scene: Ignore general coordinator updates."""
        pass