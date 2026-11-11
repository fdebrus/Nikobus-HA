"""What discovery found: modules, keys, scenes and imported names.

The library's discovery writes into two dicts the host owns, in shapes
of its own (``{"nikobus_module": {address: entry}}`` and
``{"nikobus_button": {plate address: entry}}``). They are kept as they
are, in one Home Assistant store, and read through this class so that
no other module depends on their layout.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from nikobus_connect.discovery import find_operation_point, is_motion_detector
from nikobus_connect.discovery.store import cf_member_set, steady_groups
from nikobus_connect.press import impacted_groups

from .const import DOMAIN, INPUT_MODULE_TYPES, OUTPUT_MODULE_TYPES

STORAGE_VERSION = 1
_SAVE_DELAY = 1.0

# Roller link modes that run the motor for the link's time, by direction.
_RUN_MODES = {"up": ("M01", "M02", "M06"), "down": ("M01", "M03", "M07")}
_RUN_TIME = re.compile(r"(\d+(?:[.,]\d+)?)\s*(s|m)\b")


@dataclass(frozen=True, slots=True)
class OutputModule:
    """A switch, dimmer or roller module."""

    address: str
    module_type: str
    channels: int
    description: str
    model: str

    @property
    def groups(self) -> tuple[int, ...]:
        """The output groups it answers for: channels 1-6, and 7-12."""
        return (1, 2) if self.channels > 6 else (1,)


@dataclass(frozen=True, slots=True)
class Key:
    """One key of a plate, or one code of a receiver."""

    plate: str
    label: str
    bus_address: str
    plate_entry: dict[str, Any]


class Inventory:
    """The stores of one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self.modules: dict[str, Any] = {"nikobus_module": {}}
        self.buttons: dict[str, Any] = {"nikobus_button": {}}
        self.scenes: dict[str, dict[str, Any]] = {}
        self.names: dict[str, Any] = {"channels": {}, "devices": {}, "scenes": []}
        #: Each output module's checksum when its links were last known good.
        self.programming: dict[str, dict[str, Any]] = {}
        #: The module store by type, the shape the library's scan planner reads.
        self.grouped: dict[str, dict[str, Any]] = {}
        self._linked: set[tuple[str, int]] | None = None

    async def async_load(self) -> None:
        """Read the store; anything missing or malformed starts empty."""
        stored = await self._store.async_load() or {}
        modules = stored.get("modules")
        if isinstance(modules, dict) and isinstance(modules.get("nikobus_module"), dict):
            self.modules = modules
        buttons = stored.get("buttons")
        if isinstance(buttons, dict) and isinstance(buttons.get("nikobus_button"), dict):
            self.buttons = buttons
        if isinstance(stored.get("scenes"), dict):
            self.scenes = stored["scenes"]
        names = stored.get("names")
        if isinstance(names, dict):
            self.names = {
                "channels": names.get("channels") or {},
                "devices": names.get("devices") or {},
                "scenes": names.get("scenes") or [],
                "areas": names.get("areas", True),
            }
        if isinstance(stored.get("programming"), dict):
            self.programming = stored["programming"]
        self.changed()

    def _payload(self) -> dict[str, Any]:
        return {
            "modules": self.modules,
            "buttons": self.buttons,
            "scenes": self.scenes,
            "names": self.names,
            "programming": self.programming,
        }

    async def async_save(self) -> None:
        """Write the stores now. The library awaits this as it fills them."""
        self.changed()
        await self._store.async_save(self._payload())

    def schedule_save(self) -> None:
        """Write the stores shortly."""
        self.changed()
        self._store.async_delay_save(self._payload, _SAVE_DELAY)

    async def async_remove(self) -> None:
        """Delete the store with its config entry."""
        await self._store.async_remove()

    def changed(self) -> None:
        """Recompute what is derived from the stores."""
        grouped: dict[str, dict[str, Any]] = {}
        for address, entry in self._module_entries().items():
            module_type = entry.get("module_type") or "other_module"
            grouped.setdefault(module_type, {})[address] = {**entry, "address": address}
        self.grouped.clear()
        self.grouped.update(grouped)
        self._linked = None

    def _module_entries(self) -> dict[str, dict[str, Any]]:
        return {
            str(address).upper(): entry
            for address, entry in self.modules["nikobus_module"].items()
            if isinstance(entry, dict)
        }

    def _plates(self) -> dict[str, dict[str, Any]]:
        return {
            str(address).upper(): entry
            for address, entry in self.buttons["nikobus_button"].items()
            if isinstance(entry, dict)
        }

    @property
    def is_empty(self) -> bool:
        """Whether no output module is known yet."""
        return not any(True for _ in self.output_modules())

    def module_type(self, address: str) -> str | None:
        """The type of any module the inventory holds."""
        entry = self._module_entries().get(address.upper())
        return entry.get("module_type") if entry else None

    def channel_count(self, address: str) -> int:
        """How many outputs a module has; 0 when it is unknown."""
        entry = self._module_entries().get(address.upper())
        channels = entry.get("channels") if entry else None
        return len(channels) if isinstance(channels, list) else 0

    def pc_link_address(self) -> str | None:
        """The address of the PC-Link the inventory names."""
        for address, entry in sorted(self._module_entries().items()):
            if entry.get("module_type") == "pc_link":
                return address
        return None

    def addresses(self) -> set[str]:
        """Every module and plate address the inventory holds."""
        return {*self._module_entries(), *self._plates()}

    def remove(self, address: str) -> bool:
        """Forget a module or a plate. A discovery brings it back if the PC-Link still lists it."""
        address = address.upper()
        found = False
        for store in (self.modules["nikobus_module"], self.buttons["nikobus_button"]):
            for key in [key for key in store if str(key).upper() == address]:
                del store[key]
                found = True
        self.programming.pop(address, None)
        if found:
            self.changed()
        return found

    def output_modules(self) -> Iterator[OutputModule]:
        """The modules whose outputs become entities."""
        for address, entry in sorted(self._module_entries().items()):
            module_type = entry.get("module_type")
            channels = self.channel_count(address)
            if module_type not in OUTPUT_MODULE_TYPES or not channels:
                continue
            yield OutputModule(
                address=address,
                module_type=module_type,
                channels=channels,
                description=self._module_label(entry, module_type),
                model=str(entry.get("model") or "").strip(),
            )

    @staticmethod
    def _module_label(entry: dict[str, Any], module_type: str) -> str:
        """What the inventory calls the product, e.g. "Switch module"."""
        info = entry.get("discovered_info")
        name = info.get("name") if isinstance(info, dict) else None
        return str(name).strip() if name else module_type.replace("_", " ").capitalize()

    def output_groups(self) -> list[tuple[str, int]]:
        """Every ``(module, group)`` whose state is read."""
        return [(module.address, group) for module in self.output_modules() for group in module.groups]

    def steady_groups(self) -> set[tuple[str, int]]:
        """The groups nothing but a key telegram changes (see the library)."""
        return steady_groups(self.buttons, self.modules)

    # --- keys ---------------------------------------------------------------

    def plate_channels(self, address: str) -> int | None:
        """How many keys a plate has, for the library's decoders."""
        entry = self._plates().get(address.upper())
        channels = entry.get("channels") if entry else None
        return channels if isinstance(channels, int) and channels > 0 else None

    def iter_keys(self) -> Iterator[Key]:
        """Every key that puts a frame on the bus."""
        for plate, entry in sorted(self._plates().items()):
            if entry.get("calendar_channel"):
                # A PC-Link calendar channel: what it sends is not relayed.
                continue
            points = entry.get("operation_points")
            if not isinstance(points, dict):
                continue
            for label, point in sorted(points.items()):
                bus_address = str((point or {}).get("bus_address") or "").upper()
                if len(bus_address) == 6:
                    yield Key(plate=plate, label=str(label), bus_address=bus_address, plate_entry=entry)

    def logical_inputs(self) -> Iterator[tuple[str, dict[str, Any], str, str]]:
        """``(plate, entry, on frame, off frame)`` of every PC-Logic or interface input.

        Such an input sends one frame when it closes (1A) and another
        when it opens (1B), and holds no level on the bus.
        """
        for plate, entry in sorted(self._plates().items()):
            if entry.get("pc_logic_parent_type") not in INPUT_MODULE_TYPES:
                continue
            points = entry.get("operation_points")
            frames = [
                str((points.get(label) or {}).get("bus_address") or "").upper() if isinstance(points, dict) else ""
                for label in ("1A", "1B")
            ]
            if all(len(frame) == 6 for frame in frames):
                yield plate, entry, frames[0], frames[1]

    def motion_detectors(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """The motion detectors with a Nikobus interface."""
        for plate, entry in sorted(self._plates().items()):
            if is_motion_detector(entry):
                yield plate, entry

    def impacted_groups(self, bus_address: str) -> list[tuple[str, int]]:
        """The output groups a key's links drive; empty when none is known."""
        hit = find_operation_point(self.buttons, bus_address.upper())
        return list(impacted_groups(hit[2])) if hit is not None else []

    def _links(self) -> Iterator[tuple[str, int, dict[str, Any]]]:
        """``(module, channel, link)`` for every link of every key."""
        for entry in self._plates().values():
            for point in (entry.get("operation_points") or {}).values():
                for link in (point or {}).get("linked_modules") or []:
                    module = str(link.get("module_address") or "").upper()
                    for output in link.get("outputs") or []:
                        channel = output.get("channel")
                        if module and isinstance(channel, int):
                            yield module, channel, output

    def is_linked(self, address: str, channel: int) -> bool:
        """Whether any key drives this output."""
        if self._linked is None:
            self._linked = {(module, channel) for module, channel, _ in self._links()}
        return (address.upper(), channel) in self._linked

    def links_read(self, address: str) -> bool:
        """Whether the module's own link table was read in full."""
        entry = self._module_entries().get(address.upper())
        return bool(entry and entry.get("links_read_at"))

    def run_time(self, address: str, channel: int, direction: str) -> float | None:
        """The longest run time a roller output's links hold for a direction.

        It is how long the module itself keeps the motor running, which
        makes it the travel time of the position estimate.
        """
        best: float | None = None
        for module, linked_channel, output in self._links():
            if (module, linked_channel) != (address.upper(), channel):
                continue
            if not str(output.get("mode") or "").startswith(_RUN_MODES[direction]):
                continue
            match = _RUN_TIME.search(str(output.get("t1") or ""))
            if not match:
                continue
            seconds = float(match.group(1).replace(",", ".")) * (60 if match.group(2) == "m" else 1)
            if seconds >= 1 and (best is None or seconds > best):
                best = seconds
        return best

    # --- names from a project file ------------------------------------------

    def channel_name(self, address: str, channel: int) -> str | None:
        """The name the project file gives an output."""
        name = self.names["channels"].get(f"{address.upper()}:{channel}")
        return name if isinstance(name, str) and name.strip() else None

    def device_name(self, address: str) -> str | None:
        """The name the project file gives a module or a plate."""
        record = self.names["devices"].get(address.upper())
        name = record.get("name") if isinstance(record, dict) else None
        return name if isinstance(name, str) and name.strip() else None

    def scene_name(self, scene: dict[str, Any]) -> str | None:
        """The name of the project file's group with exactly this scene's outputs and modes."""
        members = cf_member_set(scene)
        for group in self.names["scenes"]:
            if members and members == frozenset((m, c, code) for m, c, code in group["members"]):
                return str(group["name"])
        return None

    def module_label(self, address: str) -> str:
        """A module as a person would point at it: its name and its address."""
        entry = self._module_entries().get(address.upper()) or {}
        name = self.device_name(address) or self._module_label(entry, str(entry.get("module_type") or "module"))
        return f"{name} ({address.upper()})"

    def device_area(self, address: str) -> str | None:
        """The room the project file puts a module or a plate in."""
        if not self.names.get("areas", True):
            return None
        record = self.names["devices"].get(address.upper())
        room = record.get("room") if isinstance(record, dict) else None
        return room if isinstance(room, str) and room.strip() else None
