"""Can an IR op point share a bus address with a wall-key frame of the same
receiver (path-1 frame match would then depend on dict order)?"""
from nikobus_connect.discovery.fileio import _compute_ir_bus_address
from nikobus_connect.discovery.protocol import record_bus_address
for rx in ("0D1C80", "0D1C00", "123440"):
    wall = {record_bus_address(rx, k): k for k in range(8)}
    hits = []
    for ch in range(0, 40):
        for bank in "ABCD":
            code = f"{ch:02d}{bank}"
            b = _compute_ir_bus_address(rx, code)
            if b in wall:
                hits.append((code, b, wall[b]))
    print(rx, "IR codes colliding with a wall-key frame:", hits[:6], len(hits))
