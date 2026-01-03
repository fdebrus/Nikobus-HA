import asyncio
from pathlib import Path

from custom_components.nikobus.discovery.fileio import (
    merge_discovered_links,
    read_json_file,
    write_json_file,
    update_module_data,
)


def test_merge_discovered_links_deduplicates_outputs(tmp_path):
    async def _run():
        file_path = tmp_path / "nikobus_button_config.json"

        class DummyConfig:
            def __init__(self, base_path: Path):
                self._base_path = base_path

            def path(self, *args):
                return str(Path(self._base_path, *args))

        class DummyHass:
            def __init__(self, base_path: Path):
                self.config = DummyConfig(base_path)

        hass = DummyHass(tmp_path)

        initial_data = {
            "nikobus_button": [
                {
                    "description": "Example Button",
                    "address": "829201",
                    "impacted_module": [{"address": "9105", "group": "1"}],
                    "discovered_info": [
                        {
                            "type": "RF Transmitter with 4 Operation Points",
                            "model": "05-312",
                            "address": "201250",
                            "channels": 4,
                            "key": "1A",
                        }
                    ],
                    "discovered_links": [
                        {
                            "module_address": "C9A5",
                            "key": 2,
                            "outputs": [
                                {
                                    "channel": 1,
                                    "mode": "M03 (Off, with operation time)",
                                    "t1": "0s",
                                    "t2": None,
                                    "payload": "OLDPAYLOAD",
                                    "button_address": "1DF256",
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        await write_json_file(str(file_path), initial_data)

        command_mapping = {
            ("829201", 2): [
                {
                    "module_address": "C9A5",
                    "channel": 1,
                    "mode": "M03 (Off, with operation time)",
                    "t1": "0s",
                    "t2": None,
                    "payload": "FF2B0258C977",
                    "button_address": "1DF256",
                },
                {
                    "module_address": "C9A5",
                    "channel": 2,
                    "mode": "M03 (Off, with operation time)",
                    "t1": "0s",
                    "t2": None,
                    "payload": "FF2B0258C977",
                    "button_address": "1DF256",
                },
                {
                    "module_address": "7777",
                    "channel": 3,
                    "mode": "M01 (On / off)",
                    "t1": "1s",
                    "t2": "2s",
                    "payload": "ABCDEF",
                    "button_address": "1DF256",
                },
            ]
        }

        updated_buttons, links_added, outputs_added = await merge_discovered_links(
            hass, command_mapping
        )

        assert updated_buttons == 1
        assert links_added == 1
        assert outputs_added == 2

        updated = await read_json_file(str(file_path))
        assert updated is not None

        button_entry = updated["nikobus_button"][0]
        discovered_links = button_entry["discovered_links"]
        assert len(discovered_links) == 2
        assert discovered_links[0]["module_address"] == "7777"
        assert discovered_links[1]["module_address"] == "C9A5"

        outputs_c9a5 = next(
            block for block in discovered_links if block["module_address"] == "C9A5"
        )["outputs"]
        assert len(outputs_c9a5) == 2
        assert outputs_c9a5[0]["channel"] == 1
        assert outputs_c9a5[0]["payload"] == "OLDPAYLOAD"
        assert outputs_c9a5[1]["channel"] == 2

    asyncio.run(_run())


def test_update_module_data_creates_module_config(tmp_path):
    async def _run():
        file_path = tmp_path / "nikobus_module_config.json"

        class DummyConfig:
            def __init__(self, base_path: Path):
                self._base_path = base_path

            def path(self, *args):
                return str(Path(self._base_path, *args))

        class DummyHass:
            def __init__(self, base_path: Path):
                self.config = DummyConfig(base_path)

        hass = DummyHass(tmp_path)

        discovered_devices = {
            "C9A5": {
                "category": "Module",
                "module_type": "switch_module",
                "device_type": "01",
                "discovered_name": "Switch Module",
                "description": "Switch Module",
                "model": "05-000-02",
                "address": "c9a5",
                "channels_count": 4,
                "channels": 4,
                "discovered": True,
                "last_seen": "2024-01-01T00:00:00+00:00",
            }
        }

        await update_module_data(hass, discovered_devices)

        data = await read_json_file(str(file_path))
        assert data is not None

        switch_modules = data.get("switch_module", [])
        assert len(switch_modules) == 1

        module = switch_modules[0]
        assert list(module.keys()) == [
            "description",
            "model",
            "address",
            "discovered_info",
            "channels",
        ]
        assert module["address"] == "C9A5"
        assert module["model"] == "05-000-02"
        assert module["discovered_info"] == {
            "name": "Switch Module",
            "device_type": "01",
            "channels_count": 4,
            "last_discovered": "2024-01-01T00:00:00+00:00",
        }
        assert len(module["channels"]) == 4
        assert module["channels"][0]["description"] == ""

    asyncio.run(_run())


def test_update_module_data_merges_without_clobbering_user_fields(tmp_path):
    async def _run():
        file_path = tmp_path / "nikobus_module_config.json"

        class DummyConfig:
            def __init__(self, base_path: Path):
                self._base_path = base_path

            def path(self, *args):
                return str(Path(self._base_path, *args))

        class DummyHass:
            def __init__(self, base_path: Path):
                self.config = DummyConfig(base_path)

        hass = DummyHass(tmp_path)

        initial_data = {
            "switch_module": [
                {
                    "description": "Custom Switch",
                    "address": "C9A5",
                    "channels": [{"description": "Living Room"}],
                    "channels_count": 1,
                    "discovered_name": "Old Name",
                }
            ],
            "dimmer_module": [],
            "roller_module": [
                {
                    "description": "Custom Roller",
                    "address": "9105",
                    "channels": [
                        {
                            "description": "Window",
                            "operation_time": "45",
                        },
                        {
                            "description": "Door",
                        },
                    ],
                }
            ],
        }

        await write_json_file(str(file_path), initial_data)

        discovered_devices = {
            "C9A5": {
                "category": "Module",
                "module_type": "switch_module",
                "device_type": "01",
                "discovered_name": "New Switch Name",
                "description": "New Switch Name",
                "model": "05-000-02",
                "address": "C9A5",
                "channels_count": 3,
                "channels": 3,
                "last_seen": "2024-01-02T00:00:00+00:00",
            },
            "9105": {
                "category": "Module",
                "module_type": "roller_module",
                "device_type": "02",
                "discovered_name": "Roller Shutter Module",
                "description": "Roller Shutter Module",
                "model": "05-001-02",
                "address": "9105",
                "channels_count": 3,
                "channels": 3,
                "last_seen": "2024-01-02T00:00:00+00:00",
            },
        }

        await update_module_data(hass, discovered_devices)

        data = await read_json_file(str(file_path))
        assert data is not None

        module = data["switch_module"][0]
        assert module["description"] == "Custom Switch"
        assert module["model"] == "05-000-02"
        assert module["discovered_info"] == {
            "name": "New Switch Name",
            "device_type": "01",
            "channels_count": 3,
            "last_discovered": "2024-01-02T00:00:00+00:00",
        }
        assert module["address"] == "C9A5"
        assert len(module["channels"]) == 3
        assert module["channels"][0]["description"] == "Living Room"
        assert module["channels"][1]["description"] == ""

        roller = data["roller_module"][0]
        assert roller["model"] == "05-001-02"
        assert roller["discovered_info"] == {
            "name": "Roller Shutter Module",
            "device_type": "02",
            "channels_count": 3,
            "last_discovered": "2024-01-02T00:00:00+00:00",
        }
        assert roller["address"] == "9105"
        assert roller["channels"][0]["operation_time"] == "45"
        assert roller["channels"][1]["operation_time"] == "60"
        assert roller["channels"][2]["operation_time"] == "60"

    asyncio.run(_run())
