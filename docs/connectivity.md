# Connectivity

**Only one client may connect to the bus at a time.**

- Direct serial/USB: `/dev/ttyUSB0`
- Network bridge: `192.168.2.50:9999`

On a dropped connection the integration reconnects with exponential back-off (5 s → 10 s → 20 s → … capped at 60 s). Entities go unavailable until the link is restored, then resume without an HA restart.

Every connection ends with a **presence probe**: after the handshake the integration sends the `#A` identity broadcast, which the gateway answers with its own status frame, and any Nikobus frame relayed meanwhile counts as well. When the port opens but nothing answers — wrong port, PC-Link unpowered, serial handle left dead by the Nikobus PC software — the integration still starts, but a Repair issue names the port and what to check. The first Nikobus frame received afterwards withdraws it, so the issue never outlives the problem.

Only one exchange is on the bus at a time: polls, user commands and discovery reads are serialised through one lock.

The **Connection** sensor on the Bridge device exposes the live status (`connected` / `reconnecting` / `disconnected`) and carries diagnostic attributes: `last_connected`, `reconnect_attempts`, and the gateway's identity from the probe, `gateway_address` (e.g. `86F5`) and `gateway_type` (`pc_link`, `feedback_module` or `pc_logic`). The connection string itself is deliberately not an attribute. For example, alert when the bus has been down for a while:

```yaml
trigger:
  - platform: state
    entity_id: sensor.nikobus_bridge_connection
    to: "disconnected"
    for: "00:05:00"
```

## Which port is Home Assistant connected to?

Three Nikobus devices have a serial port, and they are not equivalent gateways:

| Gateway | Inventory (*1. Load Project Overview*) | Relays bus traffic | Pushed state from a Feedback Module | State refresh |
|---|---|---|---|---|
| **PC-Link** (05-200) | From its registry | Everything, including the Feedback Module's own queries | Yes — enable the option | Push, or polling without a Feedback Module |
| **Feedback Module** (05-207) port | Fallback files only | Answers, never its own queries | **No** — leave the option off | Polling |
| **PC-Logic** (05-201) port | Fallback files only | Yes | Not verified | Polling |

The middle row is the trap. A Feedback Module keeps polling the outputs it tracks and its port relays the answers, but only the query it never relays says which output group (channels 1–6 or 7–12) an answer belongs to. With the option on, the two halves of every 12-channel module get swapped at the module's polling rhythm: a dimmer shown at 100 % that never moved, a light toggling on and off every minute. The integration notices pushed answers with no query behind them and raises *Home Assistant is connected to the Feedback Module's port*; turn the option off and let Home Assistant poll. Link scanning works on every gateway, since it reads the output modules directly.

![TCP bridge example 1](https://github.com/fdebrus/Nikobus-HA/assets/33791533/10c79eaf-3362-4891-b5da-1b827faae8d1)
![TCP bridge example 2](https://github.com/fdebrus/Nikobus-HA/assets/33791533/9c0b11ad-0a1c-4728-ab5e-5e68be6452a8)
