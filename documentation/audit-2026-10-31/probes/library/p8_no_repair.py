"""The store p1_history.sh leaves (fdebrus plate 180C56 stamped key_layout 0_4
by the 0.57.2 inventory) is not repaired by a 0.57.2 link scan when records
go through the real decoder with a coordinator that knows the channel count."""
import json, sys, os
from nikobus_connect.discovery.discovery import add_to_command_mapping
from nikobus_connect.discovery.fileio import merge_linked_modules
from nikobus_connect.discovery.protocol import decode_command_payload
data = json.load(open(os.path.join(os.path.dirname(__file__), "p1_store.json")))
class Coord:
    def get_button_channels(self, a):
        e = data["nikobus_button"].get(a.upper()); return e and e.get("channels")
    def get_module_channel_count(self, a): return 12
cm = {}
for rec in ("FF16F0583160", "FF36F0583160"):   # key index 1 and 3, channel 7
    add_to_command_mapping(cm, decode_command_payload(rec, "switch_module", Coord(), module_address="C9A5",
                                                      module_channel_count=12), "C9A5", None)
print("mapping keys:", list(cm))
_, links, outs, unmatched = merge_linked_modules(data, cm)
e = data["nikobus_button"]["180C56"]
print("after link scan:", {l: o["bus_address"] for l, o in e["operation_points"].items()}, "key_layout", e.get("key_layout"),
      "| unmatched", unmatched)
