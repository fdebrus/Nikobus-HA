# Scenes

Scene entities come from up to three sources. Most installs are covered by the first two, which need no configuration.

## CF broadcasts (auto-extracted from the bus)

A Nikobus **Central Function (CF)** is a multi-output activation that already lives on the bus — every "scene" key on a keypad is a CF. **2. Load Existing Installation** reads them from the module link tables, classifies them (`switch_pair` / `roller_pair`), and persists them to `.storage/nikobus.cfs`. They appear as scene entities automatically, e.g.:

- `Nikobus switch CF 384102 (22 ch)`
- `Nikobus roller CF 3880CB (6 ch)`

Metadata is exposed as attributes:

```yaml
bus_address: "3880CB"
pattern: "roller_pair"
member_count: 6
outputs:
  - { module_address: "8B9C", channel: 5, mode: "M02 (Open)" }
```

**Activating a CF from HA is indistinguishable from a physical press** — the integration emits the same `#N<address>\r#E1` frame, so modules fire atomically on the bus (no per-channel fan-out, no partial-activation risk). Roller travel uses each module's stored M01/M02/M03 timings. No configuration needed; CFs are re-extracted on every scan.

> CF scenes get generic names (`Nikobus switch CF …`). [Import from your `.nkb`](nkb-import.md) to give them their real names — and to surface scenes the bus alone can't (next).

## `.nkb`-sourced scenes (named light + shutter/master scenes)

Light scenes self-identify on the bus (their preset-recall modes), so the step above already surfaces them. **Shutter / "all-off" / master scenes have no such fingerprint** — on the bus they're indistinguishable from an ordinary multi-output button — so discovery can't tell they're scenes. Your `.nkb` *does* mark them (the Central-Function grouping).

Selecting **Scenes** during a [`.nkb` import](nkb-import.md) uses that information to:

- **name** matched light-scene CFs with their real project names, and
- **create** the shutter / master scenes that discovery couldn't, as real scene entities (keyed on the address that fires them, sourced from the `.nkb` and preserved across future re-discovery).

## User-authored scenes (`nikobus_scene_config.json`)

For HA-side per-channel groupings that **don't** exist as a CF on the bus. **Create, edit and delete them from the UI**: *Settings → Devices & Services → Nikobus → Configure → Manage scenes* — pick the module, channel and state per member; the integration writes the file and reloads the scene entities for you.

The file lives in `/config` and is loaded at startup; a missing/empty file is fine, and you can still edit it by hand if you prefer. Dimmers use 0–255, switches `"on"`/`"off"`, shutters `"open"`/`"close"`/`"stop"`.

```json
{
  "scene": [
    {
      "id": "scene_close_all_shutters",
      "description": "Close all shutters",
      "channels": [
        {"module_id": "9105", "channel": "1", "state": "close"},
        {"module_id": "9105", "channel": "2", "state": "close"}
      ]
    }
  ]
}
```

Activation sends one command per channel (HA-driven fan-out), touching only the channels you list.

**Which one?** A scene that already exists in Nikobus → it's a CF, no config needed (import the `.nkb` for its name). A new cross-module grouping that doesn't exist in Nikobus → write it in the JSON file.

> **Upgrading to 3.0.0:** light-scene CFs are now keyed on the address the bus actually emits (the wire form), which both fixes activation and splits a multi-key trigger into one scene per key. As a result a light-scene's `unique_id` (and entity id) **changes on the first discovery after upgrade** — the old `scene.nikobus_cf_…` entity is replaced by one or more new ones. Re-point any automation or dashboard that referenced the old entity. CF *switch/roller* scenes are unaffected.
