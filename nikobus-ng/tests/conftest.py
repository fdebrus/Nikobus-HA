"""Fixtures: a real Home Assistant, the real library, a simulated bus.

Nothing of Home Assistant or of nikobus-connect is replaced. The bus is
``nikobus-simulator``, which speaks the PC-Link protocol over TCP and
answers from the memory a real module would hold.

What the simulator does not model, and so what these tests do not
cover: the PC-Link's own inventory (the first pass of a discovery), a
Feedback Module's reads, and anything about timing on a serial line.
The inventory is therefore seeded into the store the way the library
writes it, with the library's own merge function.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from custom_components.nikobus_ng.const import (
    CONF_CONNECTION,
    CONF_FEEDBACK_MODULE,
    DOMAIN,
)
from custom_components.nikobus_ng.hub import NikobusHub
from homeassistant.core import HomeAssistant
from nikobus_connect.discovery.fileio import merge_discovered_buttons
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address
from nikobus_simulator.gateway import PRESS_DEBOUNCE
from nikobus_simulator.server import NikobusSimulator
from nikobus_simulator.topology import load_installation
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_ID = "nikobus_test"
SWITCH, DIMMER, ROLLER = "4707", "0E6C", "9105"
#: An audio distribution module with one zone. Its triggers are read out
#: of the module by the link scan, like a real one's.
AUDIO = "8334"
#: Four four-key plates and the frame the key 1C of each sends. The
#: simulator matches a press on the frame a link names and only encodes
#: frames of key code 0, which is 1C on a four-key plate; so every link
#: here is on a 1C key, each on a plate of its own.
PLATES = {"1843B4": "0B7086", "007234": "0B1380", "027234": "0B1390", "017234": "0B13A0", "037234": "0B13B0"}
PLATE = "1843B4"
KEY_SWITCH_1, KEY_SWITCH_8, KEY_DIMMER, KEY_ROLLER, KEY_SCENE = PLATES.values()

#: ``mode`` is the raw mode a record stores: 4 impulse and 11 light scene
#: on (M14) on a switch module, 0 dim on/off on a dimmer, 0
#: open-stop-close on a roller.
INSTALLATION: dict[str, Any] = {
    "gateway": {"address": "86F5", "family": "pc_link"},
    "modules": [
        {
            "address": SWITCH,
            "type": "switch_module",
            "channels": 12,
            "links": [
                {"button": KEY_SWITCH_1, "key": 0, "channel": 1, "mode": 4},
                {"button": KEY_SWITCH_8, "key": 0, "channel": 8, "mode": 4},
                {"button": KEY_SCENE, "key": 0, "channel": 5, "mode": 11},
                {"button": KEY_SCENE, "key": 0, "channel": 9, "mode": 11},
            ],
        },
        {
            "address": DIMMER,
            "type": "dimmer_module",
            "channels": 12,
            "links": [{"button": KEY_DIMMER, "key": 0, "channel": 1, "mode": 0}],
        },
        {
            "address": ROLLER,
            "type": "roller_module",
            "channels": 6,
            "links": [{"button": KEY_ROLLER, "key": 0, "channel": 1, "mode": 0}],
        },
        {"address": AUDIO, "type": "audio_module", "zones": 1},
    ],
}

_PRODUCTS = {"switch_module": ("Switch module", "05-000-02", 12), "dimmer_module": ("Dim controller module", "05-007-02", 12), "roller_module": ("Roller shutter module", "05-001-02", 6)}


def seeded_inventory() -> dict[str, Any]:
    """The stores as the inventory pass leaves them: modules and plates, no links."""
    modules: dict[str, Any] = {}
    for spec in INSTALLATION["modules"]:
        if spec["type"] == "audio_module":
            modules[spec["address"]] = {"module_type": "audio_module", "description": "audio_module_1", "model": "05-205"}
            continue
        name, model, channels = _PRODUCTS[spec["type"]]
        modules[spec["address"]] = {
            "module_type": spec["type"],
            "description": f"{spec['type']}_1",
            "model": model,
            "channels": [{"description": f"not_in_use output_{n}"} for n in range(1, channels + 1)],
            "discovered_info": {"name": name, "channels_count": channels},
        }
    buttons: dict[str, Any] = {"nikobus_button": {}}
    product = DEVICE_TYPES["06"]
    merge_discovered_buttons(
        buttons,
        {
            plate: {"category": "Button", "device_type": "06", "model": product["Model"], "channels": product["Channels"], "description": product["Name"]}
            for plate in PLATES
        },
        KEY_MAPPING,
        convert_nikobus_address,
    )
    return {"modules": {"nikobus_module": modules}, "buttons": buttons, "scenes": {}, "names": {}}


class WallSimulator(NikobusSimulator):
    """The simulator, with a hand that presses wall keys.

    A press is applied to the modules through their link tables, as a
    host press is, and relayed to the host as a burst of ``#N`` frames
    40 ms apart. Not modelled: a held key's longer burst, two keys at
    once, a frame lost on the way.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clients: list[asyncio.StreamWriter] = []
        #: The keys the host pressed, in order. A press is a burst of the
        #: same frame, as a held key is: the burst counts once.
        self.host_presses: list[str] = []
        self._last_frame: tuple[str, float] = ("", 0.0)
        handle = self.gateway.handle

        def _recording(line: str) -> list[str]:
            if line.strip().startswith("#N"):
                frame, now = line.strip()[2:8].upper(), time.monotonic()
                if (frame, True) != (self._last_frame[0], now - self._last_frame[1] < PRESS_DEBOUNCE):
                    self.host_presses.append(frame)
                self._last_frame = (frame, now)
            return handle(line)

        self.gateway.handle = _recording  # type: ignore[method-assign]

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.append(writer)
        try:
            await super()._serve_client(reader, writer)
        finally:
            self._clients.remove(writer)

    async def stop(self) -> None:
        # A server waits for its clients to go; a failed test leaves one.
        for writer in list(self._clients):
            writer.close()
        await super().stop()

    async def wall_press(self, bus_address: str, frames: int = 3) -> None:
        for module in self.gateway.modules.values():
            module.apply_press(bus_address)
        for _ in range(frames):
            for writer in self._clients:
                writer.write(f"#N{bus_address}\r".encode("ascii"))
                await writer.drain()
            await asyncio.sleep(0.04)

    def output(self, address: str, channel: int) -> int:
        return self.gateway.modules[address].outputs[channel - 1]


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration under test."""


@pytest.fixture(autouse=True)
def _quick_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pauses a serial line needs and a socket does not."""
    monkeypatch.setattr("nikobus_connect.connection.PRESENCE_PROBE_SETTLE", 0.0)
    monkeypatch.setattr("nikobus_connect.const.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)


@pytest.fixture
def quick_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give up on a silent module in well under a second instead of fifteen."""
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ACK_WAIT_TIMEOUT", 0.6)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ATTEMPT_TIMEOUT", 0.2)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ANSWER_WAIT_TIMEOUT", 0.2)


@pytest.fixture
async def simulator(socket_enabled: None) -> AsyncIterator[WallSimulator]:
    """A running simulated installation, on a real local socket."""
    async with WallSimulator(load_installation(INSTALLATION)) as running:
        yield running


@pytest.fixture
def entry(hass: HomeAssistant, simulator: WallSimulator, hass_storage: dict[str, Any]) -> MockConfigEntry:
    """A config entry pointing at the simulator, its inventory already read."""
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": seeded_inventory(),
    }
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        unique_id=simulator.connection_string,
        title="Nikobus",
        data={CONF_CONNECTION: simulator.connection_string, CONF_FEEDBACK_MODULE: False},
    )
    config_entry.add_to_hass(hass)
    return config_entry


async def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Wait for the bus: sockets are real, so is the time they take."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


SetupFixture = Callable[[], Awaitable[NikobusHub]]


@pytest.fixture
def setup(hass: HomeAssistant, entry: MockConfigEntry) -> SetupFixture:
    """Set the entry up and wait until every output group was read once."""

    async def _setup() -> NikobusHub:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hub: NikobusHub = entry.runtime_data
        await wait_for(lambda: all(hub.value(a, 1 if g == 1 else 7) is not None for a, g in hub.inventory.output_groups()))
        await hass.async_block_till_done()
        return hub

    return _setup


@pytest.fixture
def scanned(hass: HomeAssistant, entry: MockConfigEntry, setup: SetupFixture, monkeypatch: pytest.MonkeyPatch) -> SetupFixture:
    """Set up, then run the link pass of a discovery against the simulator.

    The inventory pass is skipped (see the module docstring); the link
    scan, the save and the reload that ends a discovery are the real ones.
    """

    async def _scanned() -> NikobusHub:
        async def _inventory_already_read(self: Any) -> None:
            self._hub.inventory.changed()

        monkeypatch.setattr("custom_components.nikobus_ng.discovery.DiscoveryRunner._async_inventory", _inventory_already_read)
        hub = await setup()
        hub.discovery.start()
        await hub.discovery.async_wait()
        await hass.async_block_till_done()
        await wait_for(lambda: entry.runtime_data is not hub and entry.runtime_data.connected)
        new_hub: NikobusHub = entry.runtime_data
        await wait_for(lambda: all(new_hub.value(a, 1 if g == 1 else 7) is not None for a, g in new_hub.inventory.output_groups()))
        await hass.async_block_till_done()
        return new_hub

    return _scanned
