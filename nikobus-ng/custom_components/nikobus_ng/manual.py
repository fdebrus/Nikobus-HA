"""The hand-written inventory files of the Nikobus integration, read for this one.

For an installation with neither a PC-Link nor a project file, the
Nikobus integration takes its modules and keys from two JSON files. They
have the shape the library builds from a project file, plus a few fields
per output that are settings there and options here.
"""

from __future__ import annotations

from typing import Any

from nikobus_connect.nkb import NkbConfig

from .const import (
    CONF_CLOSE_TIME,
    CONF_END_MARGIN,
    CONF_FAN_OUTPUTS,
    CONF_LED_KEYS,
    CONF_LIGHT_OUTPUTS,
    CONF_OFF_KEY,
    CONF_ON_KEY,
    CONF_OPEN_TIME,
    CONF_SHUTTERS,
    CONF_USE_AS,
    DIMMER_MODULE,
    MODULE_CHANNELS,
    ROLLER_MODULE,
    SWITCH_MODULE,
    USE_AS_LIGHT,
    USE_AS_SWITCH,
)


def _seconds(value: Any) -> float | None:
    """A run time written as a number or as text; ``None`` when there is none."""
    try:
        seconds = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def inventory_from_files(modules: Any, buttons: Any) -> tuple[NkbConfig, dict[str, Any], dict[str, Any]]:
    """``(inventory, names, options)`` from the two files' contents.

    Raises when a file is not what it should be, so that nothing is
    replaced by half of an inventory.
    """
    if not isinstance(modules, dict) or not isinstance(buttons, dict):
        raise TypeError("each file holds one JSON object")
    keys = buttons.get("nikobus_button")
    if not isinstance(keys, list) or not all(isinstance(k, dict) and k.get("address") for k in keys):
        raise ValueError("the button file lists its keys under nikobus_button, each with an address")

    module_config: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, Any] = {"devices": {}, "channels": {}}
    lights: list[str] = []
    fans: list[str] = []
    shutters: dict[str, dict[str, Any]] = {}
    led_keys: dict[str, dict[str, str]] = {}
    for module_type in MODULE_CHANNELS:
        for entry in modules.get(module_type) or []:
            address = str(entry["address"]).strip().upper()
            channels = entry["channels"]
            if len(address) != 4 or len(channels) not in MODULE_CHANNELS[module_type]:
                raise ValueError(f"module {address}: not a {module_type} with that many outputs")
            if description := str(entry.get("description") or "").strip():
                names["devices"][address] = {"name": description, "room": ""}
            for number, channel in enumerate(channels, start=1):
                output = f"{address}:{number}"
                name = str(channel.get("description") or "").strip()
                if name and not name.lower().startswith(("not_in_use", "output ")):
                    names["channels"][output] = name
                kind = str(channel.get("entity_type") or "").strip().lower()
                if module_type == SWITCH_MODULE and kind == USE_AS_LIGHT:
                    lights.append(output)
                elif module_type == DIMMER_MODULE and kind == "fan":
                    fans.append(output)
                if led := {
                    option: str(channel[field]).strip().upper()
                    for option, field in ((CONF_ON_KEY, "led_on"), (CONF_OFF_KEY, "led_off"))
                    if str(channel.get(field) or "").strip()
                }:
                    led_keys[output] = led
                if module_type != ROLLER_MODULE:
                    continue
                settings: dict[str, Any] = {}
                if kind in (USE_AS_SWITCH, USE_AS_LIGHT):
                    settings[CONF_USE_AS] = kind
                both = _seconds(channel.get("operation_time"))
                for option, field in ((CONF_OPEN_TIME, "operation_time_up"), (CONF_CLOSE_TIME, "operation_time_down")):
                    if seconds := _seconds(channel.get(field)) or both:
                        settings[option] = seconds
                if (margin := channel.get("end_stop_margin")) not in (None, ""):
                    settings[CONF_END_MARGIN] = float(str(margin).replace(",", "."))
                if settings:
                    shutters[output] = settings
            module_config.setdefault(module_type, []).append(
                {
                    "address": address,
                    "description": entry.get("description") or "",
                    "model": entry.get("model") or "",
                    "channels": [{"description": ""} for _ in channels],
                }
            )
    if not module_config:
        raise ValueError("the module file holds no switch, dimmer or roller module")
    options = {
        CONF_LIGHT_OUTPUTS: sorted(lights),
        CONF_FAN_OUTPUTS: sorted(fans),
        CONF_SHUTTERS: shutters,
        CONF_LED_KEYS: led_keys,
    }
    return NkbConfig(module_config=module_config, button_config={"nikobus_button": keys}), names, options
