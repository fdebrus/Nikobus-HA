import os
import json
import logging
import aiofiles

_LOGGER = logging.getLogger(__name__)

async def write_json_file(file_path, data):
    """Write JSON data to a file asynchronously."""
    try:
        async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
            await file.write(json.dumps(data, indent=4))
        _LOGGER.info("Data written to file: %s", file_path)
    except Exception as e:
        _LOGGER.error("Failed to write data to file %s: %s", file_path, e)

async def read_json_file(file_path):
    """Read JSON data from a file asynchronously. Returns dict or None on error."""
    if not os.path.exists(file_path):
        return None
    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
            return json.loads(await file.read())
    except Exception as e:
        _LOGGER.error("Failed to read data from file %s: %s", file_path, e)
        return None

async def update_module_data(hass_config_path, discovered_devices):
    """
    Write discovered module data to a JSON file.
    Expects discovered_devices to be a dict with address as key.
    """
    module_data = {
        "switch_module": {},
        "dimmer_module": {},
        "roller_module": {},
        "other_module": {},
    }
    for device in discovered_devices.values():
        if device.get("category") == "Button":
            continue
        address = device.get("address")
        description = device.get("description", "")
        if "Switch Module" in description or "Compact Switch Module" in description:
            module_data["switch_module"][address] = device
        elif "Dimmer Module" in description or "Compact Dim Controller" in description:
            module_data["dimmer_module"][address] = device
        elif "Roller Shutter Module" in description:
            module_data["roller_module"][address] = device
        else:
            module_data["other_module"][address] = device

    file_path = os.path.join(hass_config_path, "nikobus_module_discovered.json")
    await write_json_file(file_path, module_data)

async def update_button_data(hass_config_path, discovered_devices, key_mapping, convert_nikobus_address):
    """
    Update the button config JSON file based on discovered devices.
    Requires key_mapping and convert_nikobus_address function from protocol/mappings.
    """
    file_path = os.path.join(hass_config_path, "nikobus_button_config.json")
    existing_data = []
    if os.path.exists(file_path):
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
                existing_json = json.loads(await file.read())
                existing_data = existing_json.get("nikobus_button", [])
                if not isinstance(existing_data, list):
                    existing_data = []
        except Exception as e:
            _LOGGER.error("Failed to read existing button data: %s", e)

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
