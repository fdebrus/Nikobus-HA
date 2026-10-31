import os
import sys, json
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
sys.path.insert(0, os.getcwd())
import conftest  # installs HA stubs
from custom_components.nikobus.nkbmanual import convert_legacy_to_flat
envelope = {"version": 1, "minor_version": 1, "key": "nikobus.modules",
  "data": {"nikobus_module": {"C9A5": {"module_type": "switch_module", "channels": [{"description": "a"}]},
                               "4707": {"module_type": "roller_module", "channels": []}}}}
print(json.dumps(convert_legacy_to_flat(envelope), indent=1)[:600])
print(convert_legacy_to_flat(envelope["data"]).keys())
