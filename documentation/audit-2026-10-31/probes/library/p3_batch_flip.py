"""_settle_two_button_layouts judges each merge_linked_modules call alone.

Discovery calls merge_linked_modules once per decoded frame batch, and
_rebuild_module_links once per module with that module's records only.
A plate whose records span both families (which the docstring says must
leave it alone) is instead flipped by whichever batch came last. Also:
registry records that merge_linked_modules then *drops* as
non-authoritative still vote.
"""
from nikobus_connect.discovery.fileio import merge_discovered_buttons, merge_linked_modules
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address

P = "1772B8"
def inv():
    d = {"nikobus_button": {}}
    s = DEVICE_TYPES["04"]
    merge_discovered_buttons(d, {P: {"category": "Button", "device_type": "04", "model": s["Model"], "channels": 2,
        "description": s["Name"]}}, KEY_MAPPING, convert_nikobus_address)
    return d
def rec(key, module, ch, src="output_module_table"):
    return {(P, key, None): [{"module_address": module, "channel": ch, "mode": "M03 (Off + Operating time)",
            "button_address": P, "record_source": src}]}
def show(tag, d):
    e = d["nikobus_button"][P]
    print(tag, {l: (o["bus_address"], sorted({(b["module_address"], x["channel"]) for b in o.get("linked_modules", []) for x in b["outputs"]})) for l, o in e["operation_points"].items()}, e.get("key_layout"))

d = inv()
merge_linked_modules(d, {**rec(0, "7D5F", 1), **rec(1, "7D5F", 2)}); show("one call, both families :", d)
d = inv()
merge_linked_modules(d, rec(0, "7D5F", 1)); show("batch 1 (key 0)        :", d)
merge_linked_modules(d, rec(1, "7D5F", 2)); show("batch 2 (key 1)        :", d)
# a dropped registry record still decides the layout
d = inv()
merge_linked_modules(d, rec(0, "7D5F", 1, src="pc_link_registry"), authoritative_modules={"7D5F"})
show("dropped registry record:", d)
