"""The installation as data: what is wired to what.

Home Assistant shows an entity and its state. What it cannot show is
the wiring the link scan read out of the modules: which key drives
which output, in which mode and for how long, and what a scene holds.
That is what lets a person, or a model, answer "which key switches the
kitchen light" or "why did the hall light go off by itself".

Everything here is read from the inventory and the registries; nothing
goes on the bus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .entity import output_platform

if TYPE_CHECKING:
    from .hub import NikobusHub
    from .inventory import Key, OutputModule


class Describer:
    """Answers questions about one installation."""

    def __init__(self, hub: NikobusHub) -> None:
        self._hub = hub
        self._entities = er.async_get(hub.hass)
        self._areas = ar.async_get(hub.hass)

    def _entity(self, platform: str, unique_id: str) -> dict[str, Any]:
        """An entity as a model can point at it: its id, its name, its area."""
        entity_id = self._entities.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id is None:
            return {"entity_id": None}
        entry = self._entities.async_get(entity_id)
        state = self._hub.hass.states.get(entity_id)
        area = self._areas.async_get_area(entry.area_id) if entry and entry.area_id else None
        return {
            "entity_id": entity_id,
            "name": state.name if state else (entry.name or entry.original_name if entry else None),
            "area": area.name if area else None,
            "enabled": bool(entry and entry.disabled_by is None),
        }

    def _output_ref(self, module: OutputModule, channel: int) -> dict[str, Any]:
        return {
            "module": module.address,
            "channel": channel,
            **self._entity(output_platform(self._hub, module, channel), f"{module.address}_{channel}"),
        }

    def _links_of(self, key: Key) -> list[dict[str, Any]]:
        """What a key drives, with the mode and the times of each link."""
        modules = {module.address: module for module in self._hub.inventory.output_modules()}
        links = []
        point = key.plate_entry["operation_points"][key.label] or {}
        for link in point.get("linked_modules") or []:
            address = str(link.get("module_address") or "").upper()
            for output in link.get("outputs") or []:
                channel = output.get("channel")
                target: dict[str, Any] = {"module": address, "channel": channel}
                if address in modules and isinstance(channel, int):
                    target = self._output_ref(modules[address], channel)
                links.append(
                    {
                        **target,
                        "mode": output.get("mode"),
                        **{name: output[name] for name in ("t1", "t2") if output.get(name)},
                    }
                )
        return links

    def key(self, key: Key) -> dict[str, Any]:
        """One key: its plate, its frame, its event entity, what it drives."""
        inventory = self._hub.inventory
        return {
            "plate": inventory.device_name(key.plate) or key.plate_entry.get("description") or key.plate,
            "plate_address": key.plate,
            "key": key.label,
            "address": key.bus_address,
            **self._entity(Platform.EVENT, f"{key.bus_address}_event"),
            "drives": self._links_of(key),
        }

    def output(self, module: OutputModule, channel: int) -> dict[str, Any]:
        """One output: what it is, its state, the keys and scenes that drive it."""
        inventory = self._hub.inventory
        drivers = []
        for key in inventory.keys_driving(module.address, channel):
            mine = [
                link
                for link in self._links_of(key)
                if (link.get("module"), link.get("channel")) == (module.address, channel)
            ]
            drivers.append(
                {
                    "plate": inventory.device_name(key.plate) or key.plate_entry.get("description") or key.plate,
                    "key": key.label,
                    "address": key.bus_address,
                    "links": [{k: v for k, v in link.items() if k in ("mode", "t1", "t2")} for link in mine],
                }
            )
        return {
            **self._output_ref(module, channel),
            "module_type": module.module_type,
            "value": self._hub.value(module.address, channel),
            "controlled_by": drivers,
            "calendar_channels": inventory.calendar_channels_driving(module.address, channel),
            "scenes": [
                scene["address"]
                for scene in self.scenes()
                if any((o.get("module"), o.get("channel")) == (module.address, channel) for o in scene["outputs"])
            ],
        }

    def scenes(self) -> list[dict[str, Any]]:
        """Every central function: its frames, and the outputs it sets."""
        inventory = self._hub.inventory
        modules = {module.address: module for module in inventory.output_modules()}
        scenes = []
        for address, record in sorted(inventory.scenes.items()):
            outputs = []
            for output in record.get("outputs") or []:
                module = str(output.get("module_address") or "").upper()
                channel = output.get("channel")
                target: dict[str, Any] = {"module": module, "channel": channel}
                if module in modules and isinstance(channel, int):
                    target = self._output_ref(modules[module], channel)
                outputs.append({**target, "mode": output.get("mode")})
            entity = self._entity(Platform.SCENE, f"{address}_scene")
            if entity["entity_id"] is None:
                entity = self._entity(Platform.COVER, f"{address}_group")
            scenes.append(
                {
                    "address": address,
                    "project_name": inventory.scene_name(record),
                    **entity,
                    "triggered_by": record.get("triggered_by") or [record.get("bus_address") or address],
                    "outputs": outputs,
                }
            )
        return scenes

    def health(self) -> dict[str, Any]:
        """Whether the bus is there, what is running, and what needs attention."""
        hub = self._hub
        status = hub.discovery.status
        issues = [
            {"id": issue.issue_id, "fixable": issue.is_fixable}
            for (domain, _), issue in ir.async_get(hub.hass).issues.items()
            if domain == DOMAIN and issue.active
        ]
        return {
            "connected": hub.connected,
            "gateway": hub.connection.gateway_family,
            "feedback_module": hub.feedback_module,
            "discovery": {"phase": status.phase, "progress": status.progress, "error": status.error},
            "clock_seconds_ahead": hub.maintenance.clock_drift,
            "programming": hub.maintenance.health,
            "reprogrammed_modules": list(hub.maintenance.reprogrammed),
            "repairs": issues,
        }

    def overview(self) -> dict[str, Any]:
        """Every output and scene, briefly: enough to find the one to ask about."""
        inventory = self._hub.inventory
        return {
            "outputs": [
                {
                    **self._output_ref(module, channel),
                    "module_type": module.module_type,
                    "value": self._hub.value(module.address, channel),
                    "keys": len(inventory.keys_driving(module.address, channel)),
                }
                for module in inventory.output_modules()
                for channel in range(1, module.channels + 1)
            ],
            "scenes": [
                {key: scene.get(key) for key in ("address", "entity_id", "name", "project_name")}
                for scene in self.scenes()
            ],
            "plates": sorted(
                {
                    inventory.device_name(key.plate) or key.plate_entry.get("description") or key.plate
                    for key in inventory.iter_keys()
                }
            ),
        }

    def installation(self) -> dict[str, Any]:
        """The whole installation."""
        inventory = self._hub.inventory
        return {
            "health": self.health(),
            "outputs": [
                self.output(module, channel)
                for module in inventory.output_modules()
                for channel in range(1, module.channels + 1)
            ],
            "keys": [self.key(key) for key in inventory.iter_keys()],
            "scenes": self.scenes(),
            "logical_inputs": [
                {"plate": plate, "on_address": on, "off_address": off, **self._entity(Platform.SWITCH, f"{plate}_input")}
                for plate, _entry, on, off in inventory.logical_inputs()
            ],
        }

    def find_output(self, entity_id: str) -> dict[str, Any] | None:
        """The output behind an entity id of this integration."""
        entry = self._entities.async_get(entity_id)
        if entry is None or entry.platform != DOMAIN:
            return None
        for module in self._hub.inventory.output_modules():
            for channel in range(1, module.channels + 1):
                if entry.unique_id == f"{module.address}_{channel}":
                    return self.output(module, channel)
        return None

    def find_key(self, reference: str) -> dict[str, Any] | None:
        """The key behind an event entity id or a six-digit frame."""
        entry = self._entities.async_get(reference) if "." in reference else None
        wanted = entry.unique_id.removesuffix("_event") if entry and entry.platform == DOMAIN else reference
        for key in self._hub.inventory.iter_keys():
            if key.bus_address == wanted.upper():
                return self.key(key)
        return None
