"""The README's manual-config examples load as documented.

The two JSON blocks under "Installs without a PC-Link" are what users
copy for an install without a PC-Link. This keeps them parseable and
checks that they produce what the README says: the entities per module
and channel option, and one 4-key button plus a single-key one.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.nikobus.nkbmanual import (
    _apply_button_config,
    convert_legacy_to_flat,
)
from custom_components.nikobus.router import build_routing

README = Path(__file__).resolve().parents[1] / "README.md"


def _examples() -> tuple[dict, dict]:
    text = README.read_text()
    section = text[text.index("### Installs without a PC-Link") :]
    section = section[: section.index("\n---\n")]
    blocks = re.findall(r"```json\n(.*?)```", section, flags=re.DOTALL)
    assert len(blocks) == 2, "expected one module and one button example"
    return json.loads(blocks[0]), json.loads(blocks[1])


def test_the_module_example_loads_and_routes_as_documented() -> None:
    modules, _ = _examples()
    flat = convert_legacy_to_flat(modules)
    assert set(flat) == {"4707", "0E6C", "9105"}
    assert [len(flat[a]["channels"]) for a in ("4707", "0E6C", "9105")] == [12, 4, 6]
    # Legacy single run time split into both directions.
    assert flat["9105"]["channels"][1]["operation_time_up"] == "45"
    assert flat["9105"]["channels"][1]["operation_time_down"] == "45"

    nested: dict[str, dict] = {}
    for address, entry in flat.items():
        nested.setdefault(entry["module_type"], {})[address] = entry
    routing = build_routing(nested)
    by_channel = {
        (spec.address, spec.channel): domain
        for domain, specs in routing.items()
        for spec in specs
    }
    assert by_channel[("4707", 1)] == "switch"
    assert by_channel[("4707", 2)] == "light"  # entity_type light
    assert ("4707", 4) not in by_channel  # not_in_use
    assert by_channel[("0E6C", 3)] == "fan"
    assert by_channel[("9105", 1)] == "cover"
    cover = next(s for s in routing["cover"] if s.channel == 1)
    assert (cover.operation_time_up, cover.operation_time_down, cover.end_stop_margin) == ("30.5", "32", "8")


def test_the_button_example_makes_one_plate_and_one_single_key() -> None:
    _, buttons = _examples()
    data: dict = {}
    with patch(
        "custom_components.nikobus.nkbmanual._read_json",
        AsyncMock(return_value=("/config/nikobus_button_config.json", buttons)),
    ):
        plates, keys, _path = asyncio.new_event_loop().run_until_complete(
            _apply_button_config(SimpleNamespace(), data)
        )
    assert (plates, keys) == (2, 5)
    plate = data["nikobus_button"]["1843B4"]
    assert plate["channels"] == 4
    assert {k: op["bus_address"] for k, op in plate["operation_points"].items()} == {
        "1A": "8B7086", "1B": "CB7086", "1C": "0B7086", "1D": "4B7086",
    }
    single = data["nikobus_button"]["2D1C80"]
    assert single["channels"] == 1


def test_the_example_key_addresses_are_the_plates_real_ones() -> None:
    from nikobus_connect.discovery.mapping import KEY_MAPPING
    from nikobus_connect.discovery.protocol import convert_nikobus_address

    _, buttons = _examples()
    base = convert_nikobus_address("1843B4")
    for entry in buttons["nikobus_button"]:
        linked = entry.get("linked_button")
        if not linked:
            continue
        offset = int(KEY_MAPPING[4][linked[0]["key"]], 16)
        expected = f"{(int(base[0], 16) + offset) & 0xF:X}{base[1:]}"
        assert entry["address"] == expected, linked[0]["key"]
