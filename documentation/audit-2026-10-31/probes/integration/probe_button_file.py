import os
import sys, json, asyncio, os, tempfile
sys.path.insert(0, os.path.join(os.getcwd(), "tests")); sys.path.insert(0, os.getcwd())
import conftest
from unittest.mock import MagicMock
from custom_components.nikobus import nkbmanual
d = tempfile.mkdtemp()
hass = MagicMock()
hass.config.path = lambda *p: os.path.join(d, *p)
async def ex(f, *a): return f(*a)
hass.async_add_executor_job = ex
store_shaped = {"nikobus_button": {"0D1C80": {"type": "Bus push button, 2 control buttons", "model": "05-061", "channels": 2,
  "operation_points": {"1A": {"bus_address": "8C5A60", "linked_modules": [{"module_address": "C9A5"}]},
                       "1B": {"bus_address": "CC5A60", "linked_modules": []}}}}}
cur={}
async def rj(h, fn): return ('x', cur['p'])
nkbmanual._read_json = rj
for payload in (store_shaped, {"nikobus_button": []}):
    cur['p']=payload
    live = {"nikobus_button": {"KEEP": {"operation_points": {}}}}
    print(asyncio.run(nkbmanual._apply_button_config(hass, live)))
    print(json.dumps(live)[:400])
