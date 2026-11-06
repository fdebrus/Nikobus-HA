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

## A key that controls another integration's entity

The blueprint **Nikobus key controls any entity** lets a wall key switch a light, switch, fan or cover of any integration, for example a Shelly relay that took over a load from a Nikobus module. Import it once, then create one automation per key from it.

[![Import the blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffdebrus%2FNikobus-HA%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffdebrus%2Fnikobus_key_controls_entity.yaml)

Without the badge: *Settings → Automations & scenes → Blueprints → Import blueprint*, and paste the file's address in [`blueprints/automation/fdebrus/`](../blueprints/automation/fdebrus/nikobus_key_controls_entity.yaml).

| Field | What to enter |
|---|---|
| Key bus address | The key's six hexadecimal digits: listen to `nikobus_button_pressed` under *Tools → Events*, press the key, and copy the event's `address`. Upper or lower case, spaces around it are ignored. |
| Press | As soon as the key is pressed (the default, and the fastest), or on release after a short or a long press. |
| What the key controls | Lights, switches, fans, covers or input booleans, picked as entities, devices or a whole area. |
| Action | Toggle, Turn on (opens a cover) or Turn off (closes a cover). Toggle flips each target on its own; for a key pair, make one automation per key, one with Turn on and one with Turn off. |

Two things to know:

- **It runs only while Home Assistant runs.** A key linked on a Nikobus module keeps switching its output without Home Assistant; a key driving another integration's entity through this automation does not.
- **The plate LED does not follow.** The Feedback Module drives an LED from Nikobus outputs only. While the old module is still installed, leaving the key's link to its now-empty channel in place keeps the LED in step with that channel; the key then switches both, and the two stay together as long as only the key switches the load.

