"""Two-button plate layout settling through the REAL decoder path.

The tests feed merge_linked_modules keys built from the plate's physical
address. In production the switch decoder asks the coordinator for the
button's channel count and, when it is known (it is, after an inventory),
returns the keyed *bus frame* as push_button_address, which becomes the
command_mapping key. _settle_two_button_layouts looks that key up as a
physical address and finds nothing.
"""
from nikobus_connect.discovery.discovery import add_to_command_mapping
from nikobus_connect.discovery.fileio import merge_discovered_buttons, merge_linked_modules
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address, decode_command_payload

class Coord:
    def __init__(self, data): self.data = data
    def get_button_channels(self, addr):
        e = self.data["nikobus_button"].get(addr.upper()); return e.get("channels") if e else None
    def get_module_channel_count(self, addr): return 12

def run(plate, record, module, with_coord=True):
    data = {"nikobus_button": {}}
    spec = DEVICE_TYPES["04"]
    merge_discovered_buttons(data, {plate: {"category": "Button", "device_type": "04", "model": spec["Model"],
        "channels": 2, "description": spec["Name"]}}, KEY_MAPPING, convert_nikobus_address)
    dec = decode_command_payload(record, "switch_module", Coord(data) if with_coord else None,
                                 module_address=module, module_channel_count=12)
    cm = {}
    add_to_command_mapping(cm, dec, module, None)
    res = merge_linked_modules(data, cm)
    e = data["nikobus_button"][plate]
    print(f"plate {plate} coord={with_coord} mapping keys {list(cm)}")
    print("   ops", {l: (o['bus_address'], [b['module_address'] for b in o.get('linked_modules', [])]) for l, o in e['operation_points'].items()},
          "key_layout", e.get("key_layout"), "unmatched", res[3])

# #184 reporter: right pair, record key index 0
run("1772B8", "FF06F0E0CA5D", "7D5F", with_coord=True)
run("1772B8", "FF06F0E0CA5D", "7D5F", with_coord=False)
