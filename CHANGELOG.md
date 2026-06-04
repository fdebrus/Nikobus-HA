# Changelog

## 3.0.0

Major release — **please read the breaking changes before upgrading.**

### ⚠️ Breaking changes

- **Legacy friendly-name import removed.** The `nikobus_module_config.json` /
  `nikobus_button_config.json` files are no longer imported to set entity
  names. Entity names now live in Home Assistant's registry and are preserved
  across reloads and re-discovery — set them in HA. The files remain **only**
  as the inventory fallback for installs without a PC-Link; the integration
  logs a warning when it finds them so you know they're otherwise unused. If
  you only kept them for names and you have a PC-Link/bridge, you can delete
  them.
- **Light-scene CF entity ids change.** Light-scene Central Functions are now
  keyed on the address the bus actually emits (the keyed "wire" form, e.g.
  `0D1C9E` → `DE4E2C`). This **fixes activation that previously did nothing**
  and splits a multi-key trigger into one scene per key. Consequently, on the
  **first discovery after upgrade** a light-scene's `unique_id`/entity id
  changes: the old `scene.nikobus_cf_…` entity is replaced by one or more new
  ones. **Re-point any automation or dashboard that referenced the old
  entity.** CF *switch/roller* scenes are unaffected.
- Requires **`nikobus-connect >= 0.22.0`**.

### Added

- **Input A/B latch switch** for PC-Logic (05-201) and Modular Interface
  (05-206) inputs — a persistent on/off mirror alongside the existing
  momentary buttons. The **A** signal latches it on, **B** latches it off, and
  `turn_on` / `turn_off` drive the matching bus frame. Tracks physical presses
  and other controllers, and survives restarts. (Assumes the input emits both
  its A and B telegrams — the normal case.)
- **Reliable simulated presses.** HA-originated presses (buttons, scenes,
  CF/light-scene activation, the latch switch) are sent as a short repeated
  burst instead of a single frame — matching how a real button behaves on the
  bus and fixing presses that "sometimes" did nothing under bus contention.
  Repeat count is configurable (Options → hardware settings; default 3).
- Light-scene Central Functions are surfaced as scene entities.
- Unrecognised button presses are logged at **INFO** ("run discovery to
  populate it") instead of DEBUG, so a newly-seen button is easy to notice.

### Fixed

- Light-scene CF activation now actually fires its linked outputs.
- Modular Interface (05-206) inputs are labelled `MI-INPUT N`, not
  `LM-INPUT N`.

### Internal

- Substantial dead-code removal, de-duplication (shared hub-device,
  routing-cache, input-naming/identity and operation-point helpers), and a
  full correctness review across the integration — no behaviour change.
