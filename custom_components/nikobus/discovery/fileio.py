import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)


def _inline_channels(json_text: str) -> str:
    """Collapse channel objects to a single line while preserving indentation."""

    def _is_simple_object(block_lines: list[str]) -> bool:
        if len(block_lines) < 3:
            return False

        closing = block_lines[-1].lstrip()
        if not (closing.startswith("}") or closing.startswith("},")):
            return False

        inner = block_lines[1:-1]
        if len(inner) > 2:
            return False

        return not any("{" in line or "}" in line for line in inner)

    lines = json_text.splitlines()
    output: list[str] = []
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if line.strip() == "{" and idx + 2 < len(lines):
            block: list[str] = [line]
            cursor = idx + 1
            while cursor < len(lines):
                block.append(lines[cursor])
                if lines[cursor].lstrip().startswith("}"):
                    break
                cursor += 1

            if _is_simple_object(block):
                indent = line[: line.index("{")]
                inner_content = " ".join(part.strip() for part in block[1:-1])
                closing = block[-1].strip()
                inline = f"{indent}{{ {inner_content} }}"
                if closing.startswith("},"):
                    inline += ","
                output.append(inline)
                idx = cursor + 1
                continue

        output.append(line)
        idx += 1

    return "\n".join(output) + ("\n" if json_text.endswith("\n") else "")


async def _write_json_atomic(file_path, data, inline_channels: bool = False):
    """Write JSON data atomically to avoid partial writes."""

    def _write(path):
        serialized = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=False)
        if inline_channels:
            serialized = _inline_channels(serialized)
        with open(path, "w", encoding="utf-8") as file:
            file.write(serialized)

    directory = os.path.dirname(file_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix="tmp_", suffix=".json")
    os.close(fd)
    try:
        await asyncio.to_thread(_write, tmp_path)
        os.replace(tmp_path, file_path)
        _LOGGER.info("Data written to file: %s", file_path)
    except Exception as e:
        _LOGGER.exception("Failed to write data to file %s", file_path)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


async def write_json_file(file_path, data, inline_channels: bool = False):
    """Write JSON data to a file asynchronously."""
    await _write_json_atomic(file_path, data, inline_channels=inline_channels)


async def read_json_file(file_path):
    """Read JSON data from a file asynchronously. Returns dict or None on error."""
    if not os.path.exists(file_path):
        return None

    def _read(path):
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    try:
        return await asyncio.to_thread(_read, file_path)
    except Exception as e:
        _LOGGER.error("Failed to read data from file %s: %s", file_path, e)
        return None


def _normalize_address(address):
    return address.strip().upper() if isinstance(address, str) else ""


async def update_module_data(hass, discovered_devices):
    """Create or merge the integration module config from discovery results."""

    def _ensure_structure(data):
        def _normalize_collection(value):
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return list(value.values())
            return []

        return {
            "switch_module": _normalize_collection(data.get("switch_module")),
            "dimmer_module": _normalize_collection(data.get("dimmer_module")),
            "roller_module": _normalize_collection(data.get("roller_module")),
            "other_module": _normalize_collection(data.get("other_module")),
        }

    def _sanitize_channel(channel: dict, module_type: str) -> dict:
        allowed_keys = {"description"}
        if module_type == "roller_module":
            allowed_keys.add("operation_time")
        return {k: v for k, v in (channel or {}).items() if k in allowed_keys and v is not None}

    def _sync_channels(channels: list, module_type: str, channels_count: int) -> list:
        if not isinstance(channels, list):
            channels = []

        if channels_count is None or channels_count <= 0:
            return []

        target_count = channels_count or len(channels)
        sanitized: list[dict] = []

        for idx in range(target_count):
            if idx < len(channels):
                channel = _sanitize_channel(channels[idx], module_type)
            else:
                channel = {}

            if module_type == "roller_module" and "operation_time" not in channel:
                channel["operation_time"] = "60"

            if "description" not in channel:
                channel["description"] = ""

            sanitized.append(channel)

        return sanitized

    def _merge_module(module_list: list, module: dict, module_type: str) -> None:
        address = _normalize_address(module.get("address"))
        if not address:
            return

        channels_count = module.get("channels_count") or module.get("channels") or 0
        try:
            channels_count = int(channels_count)
        except (TypeError, ValueError):
            channels_count = 0

        timestamp = module.get("last_seen") or datetime.now(timezone.utc).isoformat()

        discovery_name = module.get("discovered_name") or module.get("description", "")
        discovery_channels = channels_count if channels_count and channels_count > 0 else None
        discovery_info = {
            "name": discovery_name,
            "device_type": module.get("device_type"),
            "last_discovered": timestamp,
        }
        if discovery_channels:
            discovery_info["channels_count"] = discovery_channels

        existing = next(
            (item for item in module_list if _normalize_address(item.get("address")) == address),
            None,
        )

        def _merge_discovery(existing_info: dict | None) -> dict:
            merged = {}
            if existing_info:
                for key in ("name", "device_type", "channels_count", "last_discovered"):
                    if key in existing_info and existing_info[key]:
                        merged[key] = existing_info[key]

            for key, value in discovery_info.items():
                if value:
                    if key == "channels_count" and value <= 0:
                        continue
                    merged[key] = value

            if "channels_count" in merged and merged["channels_count"] <= 0:
                merged.pop("channels_count", None)

            return merged

        def _module_base(description_value: str, model_value: str, channels_value: list) -> dict:
            base = {
                "description": description_value,
                "model": model_value,
                "address": address,
                "discovered_info": _merge_discovery(existing.get("discovered_info") if existing else None),
            }
            sanitized_channels = _sync_channels(channels_value, module_type, channels_count)
            if sanitized_channels:
                base["channels"] = sanitized_channels
            return base

        description = module.get("description", module.get("discovered_name", ""))
        model_value = module.get("model", "")

        if existing:
            description = existing.get("description", description)
            model_value = existing.get("model", model_value)
            merged_module = _module_base(description, model_value, existing.get("channels") or [])
            existing.clear()
            existing.update(merged_module)
        else:
            new_module = _module_base(description, model_value, [])
            module_list.append(new_module)

    filtered_devices = [
        device
        for device in discovered_devices.values()
        if device.get("category") == "Module"
    ]

    file_path = hass.config.path("nikobus_module_config.json")
    existing_data = await read_json_file(file_path) or {}
    module_data = _ensure_structure(existing_data)

    for device in filtered_devices:
        module_type = device.get("module_type", "other_module")
        target_list = module_data.get(module_type)
        if target_list is None:
            target_list = module_data["other_module"]
        _merge_module(target_list, device, module_type)

    await write_json_file(file_path, module_data, inline_channels=True)


async def update_button_data(hass, discovered_devices, key_mapping, convert_nikobus_address):
    """
    Update the button config JSON file based on discovered devices.
    Requires key_mapping and convert_nikobus_address function from protocol/mappings.
    """
    file_path = hass.config.path("nikobus_button_config.json")
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    existing_data = []
    if os.path.exists(file_path):
        existing_json = await read_json_file(file_path)
        if existing_json:
            existing_data = existing_json.get("nikobus_button", [])
            if not isinstance(existing_data, list):
                existing_data = []

    updated_data = existing_data.copy()
    lookup = {
        button.get("address"): button
        for button in updated_data
        if "address" in button
    }

    for device_address, device in discovered_devices.items():
        if device.get("category") != "Button":
            continue
        description = device.get("description", "")
        model = device.get("model", "")
        num_channels = device.get("channels", 0)
        # Key labeling logic
        if num_channels == 1:
            keys = ["1A"]
        elif num_channels == 2:
            keys = ["1A", "1B"]
        elif num_channels == 4:
            keys = ["1A", "1B", "1C", "1D"]
        elif num_channels == 8:
            keys = ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"]
        else:
            _LOGGER.error(f"Unexpected number of channels: {num_channels} for device {device_address}")
            continue
        mapping = key_mapping.get(num_channels, {})
        channels_data = {}
        converted_address = convert_nikobus_address(device_address)
        original_nibble = int(converted_address[0], 16)
        for idx, key in enumerate(keys, start=1):
            if key in mapping:
                add_value = int(mapping[key], 16)
                new_nibble_value = original_nibble + add_value
                new_nibble_hex = f"{new_nibble_value:X}"
                updated_addr = new_nibble_hex + converted_address[1:]
                channels_data[f"channel_{idx}"] = {
                    "key": key,
                    "address": updated_addr,
                }
        device["channels_data"] = channels_data
        for channel_info in channels_data.values():
            discovered_channel_address = channel_info["address"]
            key = channel_info["key"]
            new_info = {
                "type": description,
                "model": model,
                "address": device_address,
                "channels": num_channels,
                "key": key,
            }
            button = lookup.get(discovered_channel_address)
            if button:
                discovered_list = button.setdefault("discovered_info", [])
                found_info = next(
                    (
                        info
                        for info in discovered_list
                        if info.get("key") == new_info["key"]
                        and info.get("address") == new_info["address"]
                    ),
                    None,
                )
                if found_info:
                    if (
                        found_info.get("type") != new_info["type"]
                        or found_info.get("model") != new_info["model"]
                        or found_info.get("channels") != new_info["channels"]
                    ):
                        found_info.update(
                            {
                                "type": new_info["type"],
                                "model": new_info["model"],
                                "channels": new_info["channels"],
                            }
                        )
                else:
                    discovered_list.append(new_info)
            else:
                new_button = {
                    "description": f"{description} #N{discovered_channel_address}",
                    "address": discovered_channel_address,
                    "impacted_module": [{"address": "", "group": ""}],
                    "discovered_info": [new_info],
                }
                updated_data.append(new_button)
                lookup[discovered_channel_address] = new_button

    output_json = {"nikobus_button": updated_data}
    await write_json_file(file_path, output_json)


def _normalize_key(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


async def merge_discovered_links(hass, command_mapping):
    """Merge discovery command mapping into nikobus_button_config.json."""

    file_path = hass.config.path("nikobus_button_config.json")

    file_exists_before = os.path.exists(file_path)
    file_size_before = os.path.getsize(file_path) if file_exists_before else 0
    _LOGGER.info("Updating button config JSON: %s", file_path)
    _LOGGER.info(
        "Button config JSON stats before update: cwd=%s exists=%s size=%s bytes",
        os.getcwd(),
        file_exists_before,
        file_size_before,
    )

    existing_json = await read_json_file(file_path)
    if existing_json is None:
        existing_json = {"nikobus_button": []}

    buttons = existing_json.get("nikobus_button", [])
    if not isinstance(buttons, list):
        buttons = []

    address_lookup = {
        _normalize_address(button.get("address")): button
        for button in buttons
        if "address" in button
    }

    updated_buttons = 0
    links_added = 0
    outputs_added = 0
    any_updates = False
    matched_addresses = set()
    unmatched_addresses = set()

    for (push_button_address, key_raw), outputs in command_mapping.items():
        if push_button_address is None:
            continue
        normalized_address = _normalize_address(push_button_address)
        button_entry = address_lookup.get(normalized_address)
        if not button_entry:
            unmatched_addresses.add(normalized_address)
            continue

        discovered_links = button_entry.setdefault("discovered_links", [])
        updated_entry = False
        matched_addresses.add(normalized_address)

        for output in outputs:
            module_address = output.get("module_address")
            if module_address is None:
                continue

            channel_number = output.get("channel")
            mode_label = output.get("mode")
            t1_val = output.get("t1")
            t2_val = output.get("t2")
            payload_val = output.get("payload")
            button_address = output.get("button_address")

            matching_block = next(
                (
                    block
                    for block in discovered_links
                    if block.get("module_address") == module_address
                    and _normalize_key(block.get("key")) == key_raw
                ),
                None,
            )

            if matching_block is None:
                matching_block = {
                    "module_address": module_address,
                    "key": key_raw,
                    "outputs": [],
                }
                discovered_links.append(matching_block)
                links_added += 1
                updated_entry = True

            output_entry = {
                "channel": channel_number,
                "mode": mode_label,
                "t1": t1_val,
                "t2": t2_val,
                "payload": payload_val,
                "button_address": button_address,
            }

            dedupe_key = (
                output_entry["channel"],
                output_entry["mode"],
                output_entry["t1"],
                output_entry["t2"],
            )
            existing_outputs = matching_block.get("outputs", [])
            existing_keys = {
                (
                    entry.get("channel"),
                    entry.get("mode"),
                    entry.get("t1"),
                    entry.get("t2"),
                )
                for entry in existing_outputs
            }
            if dedupe_key not in existing_keys:
                existing_outputs.append(output_entry)
                matching_block["outputs"] = existing_outputs
                outputs_added += 1
                updated_entry = True

        if updated_entry:
            discovered_links.sort(
                key=lambda block: (
                    block.get("module_address", ""),
                    _normalize_key(block.get("key"))
                    if isinstance(_normalize_key(block.get("key")), int)
                    else float("inf"),
                )
            )
            for block in discovered_links:
                block_outputs = block.get("outputs", [])
                block_outputs.sort(
                    key=lambda output: (
                        output.get("channel")
                        if output.get("channel") is not None
                        else -1,
                        output.get("mode", ""),
                    )
                )
                block["outputs"] = block_outputs

            updated_buttons += 1
            any_updates = True

    if any_updates:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        await _write_json_atomic(file_path, {"nikobus_button": buttons})

    file_exists_after = os.path.exists(file_path)
    file_size_after = os.path.getsize(file_path) if file_exists_after else 0
    _LOGGER.info(
        "Button config JSON stats after update: exists=%s size=%s bytes",
        file_exists_after,
        file_size_after,
    )

    if not any_updates:
        _LOGGER.info(
            "Button config JSON updater ran: changes=0 (updated_buttons=%d, links_added=%d, outputs_added=%d)",
            updated_buttons,
            links_added,
            outputs_added,
        )
    else:
        _LOGGER.info(
            "Button config JSON summary: updated_buttons=%d, links_added=%d, outputs_added=%d",
            updated_buttons,
            links_added,
            outputs_added,
        )

    if not matched_addresses:
        unmatched_sample = list(unmatched_addresses)[:5]
        _LOGGER.debug(
            "Button config JSON updater found no matching buttons. unmatched_count=%d sample=%s",
            len(unmatched_addresses),
            unmatched_sample,
        )

    return updated_buttons, links_added, outputs_added
