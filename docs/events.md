# Events & automations

The integration fires structured HA bus events for the full press lifecycle:

- **Base**: `nikobus_button_pressed`, `nikobus_button_released`.
- **Classified**: `nikobus_short_button_pressed` (< 1 s), `nikobus_long_button_pressed` (≥ 1 s). The threshold is `SHORT_PRESS_S` in the library's `nikobus_connect.press`, which also holds the frame cadence and the release patience.
- **Release buckets**: `nikobus_button_pressed_0` (< 1 s) … `_3` (≥ 3 s).
- **Hold milestones** (while still held): `nikobus_button_timer_1` / `_2` / `_3` at 1/2/3 s. These count wire frames (40 ms cadence), not wall-clock, so a bridge that buffers and bursts frames still classifies correctly.
- **Module reads**: `nikobus_button_operation` when a read of an impacted module is scheduled — on the initial press and again on the release — for presses seen on the bus only, never for a press Home Assistant sent itself. Its payload adds `impacted_module_address` and `impacted_module_group`.
- **Scene triggers**: `nikobus_scene_activated` when one of a central-function scene's trigger addresses is seen on the bus (a press at the wall, or sent by another client); a scene Home Assistant activates itself fires no event, since the gateway never relays the host's own frame. Its payload differs from the button events: `address` (the trigger seen), `name` (the scene's name), `entity_id` (its scene entity) and `member_count` (how many outputs it sets).

All button events share one payload schema:

```yaml
address: "004E2C"        # button address (uppercase hex, no 0x)
module_address: "9105"   # impacted module, or null
channel: 1
ts: "2024-05-01T12:00:00Z"
press_id: "004E2C-..."
state: "pressed" | "released" | "timer"
duration_s: 1.2          # null on initial press
bucket: 1                # 0/1/2/3, else null
threshold_s: 2           # timer milestone, else null
source: "nikobus"
```

> **Always quote button addresses in YAML.** A value like `25E952` parses as scientific notation and becomes `null`. Use a leading space inside the quotes (`address: " 25E952"`). Addresses with any letter other than `E` are unaffected.

## Examples

**Toggle a light on a press**

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "004E2C"
action:
  - service: homeassistant.toggle
    target:
      entity_id: light.example_light
```

**Hold-to-dim (fires after 2 s held)**

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_timer_2
    event_data:
      address: "004E2C"
action:
  - service: light.turn_on
    target:
      entity_id: light.living_room_dimmer
    data:
      brightness_pct: 30
      transition: 1
```

**Drive a scene from a physical button (after sunset)**

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: " 25E952"   # leading space avoids YAML scientific-notation
condition:
  - condition: sun
    after: sunset
action:
  - service: scene.turn_on
    target:
      entity_id: scene.scene_close_all_shutters
```

**Move a cover to an exact position** (the virtual travel calculator stops it at the target):

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "C9A5"
action:
  - service: cover.set_cover_position
    target:
      entity_id: cover.living_room_blind
    data:
      position: 60
```
