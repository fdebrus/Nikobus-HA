import asyncio

from custom_components.nikobus.discovery.fileio import (
    merge_discovered_links,
    read_json_file,
    write_json_file,
)


def test_merge_discovered_links_deduplicates_outputs(tmp_path):
    async def _run():
        file_path = tmp_path / "nikobus_button_config.json"

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
            str(tmp_path), command_mapping
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
