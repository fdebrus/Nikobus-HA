from custom_components.nikobus.discovery.discovery import NikobusDiscovery


class DummyCommandQueue:
    async def clear_command_queue(self):  # pragma: no cover - test stub
        return None

    async def clear_inventory_commands_for_prefix(self, _):  # pragma: no cover
        return None


class DummyCoordinator:
    def __init__(self):
        self.discovery_running = False
        self.discovery_module = False
        self.discovery_module_address = None
        self.nikobus_command = DummyCommandQueue()
        self.dict_module_data = {"pc_link": {}}


def test_device_address_inventory_records_pc_link(monkeypatch):
    coordinator = DummyCoordinator()
    discovery = NikobusDiscovery(None, coordinator)

    monkeypatch.setattr(discovery, "_schedule_inventory_timeout", lambda: None)

    discovery.handle_device_address_inventory("\x02$18A1B2\x03")

    assert "B2A1" in discovery.discovered_devices
    recorded = discovery.discovered_devices["B2A1"]
    assert recorded["module_type"] == "pc_link"
    assert recorded["device_type"] == "0A"
    assert recorded["category"] == "Module"
