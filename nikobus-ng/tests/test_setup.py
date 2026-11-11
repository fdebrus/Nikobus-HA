"""Setting the entry up against the simulator, and taking it down."""

from __future__ import annotations

from custom_components.nikobus_ng.const import (
    CONF_CONNECTION,
    CONF_FEEDBACK_MODULE,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    DIMMER,
    KEY_SWITCH_1,
    PLATE,
    ROLLER,
    SWITCH,
    SetupFixture,
    WallSimulator,
)


async def test_the_inventory_becomes_devices_and_entities(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    assert entry.state is ConfigEntryState.LOADED
    assert hub.connection.gateway_family == "pc_link"

    registry = er.async_get(hass)
    by_domain: dict[str, int] = {}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        by_domain[entity.domain] = by_domain.get(entity.domain, 0) + 1
    assert by_domain == {
        "switch": 12,
        "light": 12,
        "cover": 6,
        "event": 16,
        "button": 17,  # discover, and one per key (not enabled)
        "sensor": 1,
        "binary_sensor": 1,
    }

    devices = dr.async_get(hass)
    module = devices.async_get_device_by_identifier((DOMAIN, SWITCH), entry.entry_id)
    gateway = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert module is not None and gateway is not None
    assert (module.name, module.model, module.via_device_id) == ("Switch module 4707", "05-000-02", gateway.id)
    assert gateway.serial_number == "86F5"
    assert devices.async_get_device_by_identifier((DOMAIN, PLATE), entry.entry_id) is not None


async def test_outputs_show_what_the_modules_report(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture) -> None:
    simulator.gateway.modules[SWITCH].outputs[8] = 0xFF  # channel 9, the second group
    simulator.gateway.modules[DIMMER].outputs[0] = 0x80
    await setup()
    assert hass.states.get("switch.switch_module_4707_output_1").state == "off"
    assert hass.states.get("switch.switch_module_4707_output_9").state == "on"
    light = hass.states.get("light.dim_controller_module_0e6c_output_1")
    assert (light.state, light.attributes["brightness"]) == ("on", 0x80)
    assert hass.states.get("cover.roller_shutter_module_9105_output_1") is not None
    assert hass.states.get("binary_sensor.nikobus_connection").state == "on"


async def test_key_press_buttons_are_not_enabled(hass: HomeAssistant, setup: SetupFixture) -> None:
    await setup()
    registry = er.async_get(hass)
    press = registry.async_get_entity_id("button", DOMAIN, f"{KEY_SWITCH_1}_press")
    assert press is not None and registry.async_get(press).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get_entity_id("event", DOMAIN, f"{KEY_SWITCH_1}_event") is not None


async def test_unloading_releases_the_connection(hass: HomeAssistant, simulator: WallSimulator, setup: SetupFixture, entry: MockConfigEntry) -> None:
    hub = await setup()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hub.connection.is_connected
    assert hass.states.get("switch.switch_module_4707_output_1").state == "unavailable"


async def test_a_gateway_that_is_not_there_is_retried(hass: HomeAssistant, socket_enabled: None, unused_tcp_port: int) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CONNECTION: f"127.0.0.1:{unused_tcp_port}", CONF_FEEDBACK_MODULE: False})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_removing_the_entry_deletes_its_store(hass: HomeAssistant, hass_storage: dict, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


async def test_roller_name_is_from_the_inventory(hass: HomeAssistant, setup: SetupFixture, entry: MockConfigEntry) -> None:
    await setup()
    roller = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, ROLLER), entry.entry_id)
    assert roller.name == "Roller shutter module 9105"
