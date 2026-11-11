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
from nikobus_connect.discovery.store import (
    cf_cover_members,
    cf_member_set,
    is_pure_roller_cf,
    steady_groups,
)
from nikobus_connect.nkb import NkbConfig
from nikobus_connect.press import impacted_groups

from .const import (
    DOMAIN,
    INPUT_MODULE_TYPES,
    MODULE_CHANNELS,
    OUTPUT_MODULE_TYPES,
    RGB_MODULE,
)

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

    def __init__(
        self, hass: HomeAssistant, entry_id: str, module_types: dict[str, str] | None = None
    ) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        #: The type the options give a module, over the one the inventory holds.
        self.module_types = {address.upper(): kind for address, kind in (module_types or {}).items()}
        #: The modules and plates the user removed, kept out through later discoveries.
        self.removed: list[str] = []
        #: The type each such module's links were last read as.
        self.links_read_as: dict[str, str] = {}
        #: What Home Assistant last wrote to each module, for a gateway that answers no state read.
        self.states: dict[str, str] = {}
        self.modules: dict[str, Any] = {"nikobus_module": {}}
        self.buttons: dict[str, Any] = {"nikobus_button": {}}
        self.scenes: dict[str, dict[str, Any]] = {}
        self.names: dict[str, Any] = {"channels": {}, "devices": {}, "scenes": []}
        #: Each output module's checksum when its links were last known good.
        self.programming: dict[str, dict[str, Any]] = {}
        #: Whether the modules and plates come from a project file, not from a PC-Link.
        self.from_project = False
        #: The module store by type, the shape the library's scan planner reads.
        self.grouped: dict[str, dict[str, Any]] = {}
        self._linked: set[tuple[str, int]] | None = None
        self._drivers: dict[tuple[str, Any], list[Key]] | None = None

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
        self.from_project = stored.get("from_project") is True
        for name in ("links_read_as", "states"):
            if isinstance(stored.get(name), dict):
                setattr(self, name, stored[name])
        if isinstance(stored.get("removed"), list):
            self.removed = [str(address).upper() for address in stored["removed"]]
        self.changed()

    def _payload(self) -> dict[str, Any]:
        return {
            "modules": self.modules,
            "buttons": self.buttons,
            "scenes": self.scenes,
            "names": self.names,
            "programming": self.programming,
            "from_project": self.from_project,
            "links_read_as": self.links_read_as,
            "states": self.states,
            "removed": self.removed,
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
        self._drivers = None

    def _module_entries(self) -> dict[str, dict[str, Any]]:
        """The modules, each as the type the options give it when they give one."""
        entries: dict[str, dict[str, Any]] = {}
        for key, entry in self.modules["nikobus_module"].items():
            if not isinstance(entry, dict):
                continue
            address = str(key).upper()
            if address in self.removed:
                continue
            kind = self.module_types.get(address)
            if kind in MODULE_CHANNELS and kind != entry.get("module_type"):
                # The outputs it has as that type: the number the inventory
                # gives when that type exists with it, else the usual one.
                channels = [c for c in entry.get("channels") or [] if isinstance(c, dict)]
                count = len(channels) if len(channels) in MODULE_CHANNELS[kind] else MODULE_CHANNELS[kind][0]
                entry = {
                    **entry,
                    "module_type": kind,
                    "channels": [*channels[:count], *({} for _ in range(count - len(channels)))],
                }
            entries[address] = entry
        return entries

    def inventoried_type(self, address: str) -> str | None:
        """The type the inventory itself holds for a module, whatever the options say."""
        for key, entry in self.modules["nikobus_module"].items():
            if str(key).upper() == address.upper() and isinstance(entry, dict):
                return entry.get("module_type")
        return None

    def types_to_read_again(self) -> list[str]:
        """The modules whose links were read as another type than the options now give them."""
        return sorted(
            address
            for address in {*self.module_types, *self.links_read_as}
            if address in self.modules["nikobus_module"]
            and self.links_read_as.get(address) != self.module_types.get(address)
            and self.module_types.get(address, self.inventoried_type(address)) in MODULE_CHANNELS
        )

    def note_links_read(self, addresses: list[str]) -> None:
        """Remember as which type each of these modules was just read."""
        for address in addresses:
            if kind := self.module_types.get(address):
                self.links_read_as[address] = kind
            else:
                self.links_read_as.pop(address, None)

    def calendar_channels_driving(self, address: str, channel: int) -> list[str]:
        """The PC-Link calendar channels with a link to this output, by their label."""
        labels = []
        for entry in self._plates().values():
            if not (label := entry.get("calendar_channel")):
                continue
            for point in (entry.get("operation_points") or {}).values():
                for link in (point or {}).get("linked_modules") or []:
                    if str(link.get("module_address") or "").upper() == address.upper() and any(
                        output.get("channel") == channel for output in link.get("outputs") or []
                    ):
                        labels.append(str(label))
        return sorted(set(labels))

    def _plates(self) -> dict[str, dict[str, Any]]:
        return {
            str(address).upper(): entry
            for address, entry in self.buttons["nikobus_button"].items()
            if isinstance(entry, dict) and str(address).upper() not in self.removed
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
        """Leave a module or a plate out from now on.

        A PC-Link goes on listing what was once in its project, so a
        discovery would bring a removed module straight back. It is kept
        out until ``restore`` is asked for it.
        """
        address = address.upper()
        if address not in self.addresses():
            return False
        self.removed.append(address)
        self.programming.pop(address, None)
        self.changed()
        return True

    def restore(self, address: str) -> bool:
        """Take a removed module or plate back in."""
        if address.upper() not in self.removed:
            return False
        self.removed.remove(address.upper())
        self.changed()
        return True

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

    def rgb_modules(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """The colour controllers: read like an output group, driven by their keys only."""
        for address, entry in sorted(self._module_entries().items()):
            if entry.get("module_type") == RGB_MODULE:
                yield address, entry

    def output_groups(self) -> list[tuple[str, int]]:
        """Every ``(module, group)`` whose state is read."""
        groups = [(module.address, group) for module in self.output_modules() for group in module.groups]
        return [*groups, *((address, 1) for address, _ in self.rgb_modules())]

    def shutter_groups(self) -> Iterator[tuple[str, dict[str, Any], list[tuple[str, int]]]]:
        """``(address, record, members)`` of the central functions that only move shutters."""
        for address, record in sorted(self.scenes.items()):
            if is_pure_roller_cf(record):
                members = [(m["module_address"], m["channel"]) for m in cf_cover_members(record)]
                if members:
                    yield address, record, members

    def audio_zones(self) -> dict[tuple[str, int], dict[str, str]]:
        """``{(audio module, zone): {function: frame}}`` from the triggers discovery filed.

        A trigger of the module itself sends the entry's own address; a
        wall key sends its own frame. Zone 0 is the module's power, not a zone.
        """
        zones: dict[tuple[str, int], dict[str, str]] = {}
        for plate, entry in self._plates().items():
            points = entry.get("operation_points")
            for label, point in (points.items() if isinstance(points, dict) else ()):
                if not isinstance(point, dict):
                    continue
                frame = str(point.get("bus_address") or plate).upper()
                for link in point.get("linked_modules") or []:
                    module = str(link.get("module_address") or "").upper()
                    records = [
                        (output.get("audio_function"), output.get("audio_zone"))
                        for output in link.get("outputs") or []
                        if isinstance(output, dict) and output.get("audio_function")
                    ]
                    if not records and label == "AUD" and entry.get("audio_function"):
                        records = [(entry.get("audio_function"), entry.get("audio_zone"))]
                    for function, zone in records:
                        if module and int(zone or 0):
                            zones.setdefault((module, int(zone)), {})[str(function)] = frame
        return zones

    def use_project(self, config: NkbConfig) -> None:
        """Take the modules and plates of a project file as the inventory.

        For an installation without a PC-Link. The file names every
        module with its outputs and every plate with the frame of each
        key; which key drives which output is still read from the modules.
        """
        modules: dict[str, Any] = {}
        for module_type, entries in config.module_config.items():
            for entry in entries:
                modules[str(entry["address"]).upper()] = {
                    "module_type": module_type,
                    "description": entry.get("description") or module_type,
                    "model": entry.get("model") or "",
                    "channels": [{"description": c.get("description") or ""} for c in entry["channels"]],
                }
        plates: dict[str, Any] = {}
        for entry in config.button_config.get("nikobus_button", []):
            frame = str(entry["address"]).upper()
            name = str(entry.get("description") or "").strip()
            face = (entry.get("linked_button") or [None])[0]
            if not isinstance(face, dict):
                # A single face: a sensor, a receiver, an interface.
                plates.setdefault(
                    frame,
                    {
                        "type": "Button",
                        "model": "",
                        "channels": 1,
                        "description": name or f"Button {frame}",
                        "operation_points": {"1A": {"bus_address": frame, "linked_modules": []}},
                    },
                )
                continue
            plate = plates.setdefault(
                str(face["address"]).upper(),
                {
                    "type": face.get("type") or "Button",
                    "model": face.get("model") or "",
                    "channels": face["channels"],
                    "description": name.rsplit(" (", 1)[0] or "Button",
                    "operation_points": {},
                },
            )
            plate["operation_points"][str(face["key"]).upper()] = {"bus_address": frame, "linked_modules": []}
        self.modules["nikobus_module"].clear()
        self.modules["nikobus_module"].update(modules)
        self.buttons["nikobus_button"].clear()
        self.buttons["nikobus_button"].update(plates)
        self.scenes = {}
        self.programming = {}
        self.removed = []
        self.from_project = True
        self.changed()

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
            if entry.get("calendar_channel") or entry.get("audio_module_address"):
                # A PC-Link calendar channel: what it sends is not relayed. A
                # trigger of an audio module: its zone is the entity, not the frame.
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

    def keys_driving(self, address: str, channel: int) -> list[Key]:
        """The keys with a link to this output, in plate order."""
        if self._drivers is None:
            self._drivers = {}
            for key in self.iter_keys():
                point = key.plate_entry["operation_points"][key.label] or {}
                for link in point.get("linked_modules") or []:
                    module = str(link.get("module_address") or "").upper()
                    for output in link.get("outputs") or []:
                        drivers = self._drivers.setdefault((module, output.get("channel")), [])
                        if key not in drivers:
                            drivers.append(key)
        return list(self._drivers.get((address.upper(), channel), []))

    def key_label(self, key: Key) -> str:
        """A key as a person would point at it: its plate, its face, its frame."""
        plate = self.device_name(key.plate) or key.plate_entry.get("description") or key.plate
        return f"{plate} {key.label} ({key.bus_address})"

    def link_statistics(self) -> dict[str, Any]:
        """How much of the installation the link scan filled in, without a name in it."""
        modes: dict[str, int] = {}
        per_module: dict[str, dict[str, int]] = {}
        for module, channel, output in self._links():
            mode = str(output.get("mode") or "unknown")
            modes[mode] = modes.get(mode, 0) + 1
            counts = per_module.setdefault(module, {"links": 0})
            counts["links"] += 1
        for module in self.output_modules():
            counts = per_module.setdefault(module.address, {"links": 0})
            linked = sum(1 for channel in range(1, module.channels + 1) if self.is_linked(module.address, channel))
            counts.update(outputs_linked=linked, outputs_unlinked=module.channels - linked)
        keys = list(self.iter_keys())
        with_links = sum(
            1 for key in keys if (key.plate_entry["operation_points"][key.label] or {}).get("linked_modules")
        )
        return {
            "modules": dict(sorted(per_module.items())),
            "keys_with_links": with_links,
            "keys_without_links": len(keys) - with_links,
            "modes": dict(sorted(modes.items())),
        }

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
