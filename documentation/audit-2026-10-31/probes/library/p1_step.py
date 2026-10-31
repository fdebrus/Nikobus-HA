"""Run one step of store history with whichever nikobus_connect is on sys.path.
usage: p1_step.py <store.json> inventory|links <plate> <device_type> [key ...]"""
import json, sys
from nikobus_connect.discovery.fileio import merge_discovered_buttons, merge_linked_modules
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address
path, step, plate, dt, *keys = sys.argv[1:]
try:
    data = json.load(open(path))
except FileNotFoundError:
    data = {"nikobus_button": {}}
if step == "inventory":
    spec = DEVICE_TYPES[dt]
    merge_discovered_buttons(data, {plate: {"category": "Button", "device_type": dt, "model": spec["Model"],
        "channels": spec["Channels"], "description": spec["Name"]}}, KEY_MAPPING, convert_nikobus_address)
else:
    cm = {(plate, int(k), None): [{"module_address": "C9A5", "channel": 7 + i, "mode": "M01 (On / off)",
          "button_address": plate, "record_source": "output_module_table"}] for i, k in enumerate(keys)}
    merge_linked_modules(data, cm)
json.dump(data, open(path, "w"))
e = data["nikobus_button"][plate]
print(step, {l: (o["bus_address"], [b["module_address"] for b in o.get("linked_modules", [])]) for l, o in e["operation_points"].items()}, "key_layout=", e.get("key_layout"))
