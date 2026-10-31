"""0.56.3 'inventory waits while identity reads are queued' is bypassed on
the normal path.

Real flow: start sets discovery_stage='inventory_addresses'; each new #A
record spawns _queue_inventory_identity_queries_for_address, which marks
the address queued and queues 96 reads -- the stage stays
'inventory_addresses'. When the window fires, _finalize_inventory_phase
only checks pending_count() for stage 'inventory_identity'; for
'inventory_addresses' with nothing pending it jumps straight to stage 2
and completes the run with 40 reads still queued.
"""
import asyncio, tempfile
from unittest.mock import MagicMock
from nikobus_connect.discovery.discovery import NikobusDiscovery

class Coord:
    nikobus_command = None; dict_module_data = {}; discovery_running = True; discovery_module = False
    discovery_module_address = None; inventory_query_type = None
    def get_module_type(self, a): return None
    def get_module_channel_count(self, a): return 12
    def get_button_channels(self, a): return 2

async def main():
    c = Coord(); cmd = MagicMock(); cmd.pending_count = MagicMock(return_value=40); c.nikobus_command = cmd
    d = NikobusDiscovery(c, config_dir=tempfile.mkdtemp(), create_task=asyncio.ensure_future,
                         button_data={"nikobus_button": {}}, module_data={"nikobus_module": {}})
    d.discovery_stage = "inventory_addresses"           # as set at run start (discovery.py:2803)
    d._inventory_addresses = {"86F5"}
    d._inventory_identity_queued = {"86F5"}             # marked by the per-address task
    completed = []
    async def fake_complete(addr): completed.append(addr)
    d._complete_discovery_run = fake_complete
    d._schedule_inventory_timeout = lambda: completed.append("rearmed")
    await d._finalize_inventory_phase()
    print("pending_count called:", cmd.pending_count.called, "| outcome:", completed)

asyncio.run(main())
