# Changelog

## 3.6.0

- **Upload your `.nkb` from the UI.** Settings → Devices & Services →
  Nikobus → **Configure → Upload .nkb project file**: pick the export (any
  filename), it's validated (must parse as a real `.nkb`) and saved as
  `nikobus.nkb` in the config directory. Then press **Import Names from
  .nkb**. No more copying files over Samba/SSH.
- **Fix: shutter / roller scenes from the `.nkb` are now created.** The
  scene member channel was read from the wrong field — roller outputs sit
  in output *pairs*, so a roller module's `ObjectAddress` runs `0,2,4,…`
  while Home Assistant numbers the rollers `1,2,3,…`. That made every
  roller-containing group (e.g. `ShuttersSalonCuisine`, `CloseHouse -
  Leave`) fail the member-set match, so 0 were created. The channel is now
  taken from the output's `Prefix` (`O02` → 2), which matches HA's
  numbering for every module type. Re-run **Import Names from .nkb**.
- **CI** (repo): ruff + pytest (py3.12/3.13) + hassfest + HACS validation
  run on every PR.

## 3.5.1

- **Imported device names keep their room** — `Entree (Living)` — *and* still
  get the Area. Nikobus names are often generic and repeated per room (an
  `Entree` in every room); 3.4.0/3.5.0 dropped the room from the name, leaving
  a wall of identical names in entity pickers / automations where the Area
  isn't shown. The room now stays in the name to disambiguate (scenes, which
  have no room, keep their bare name). Re-run **Import Names from .nkb** to
  apply.

## 3.5.0

**`.nkb`-sourced scenes — shutter & master scenes now import as real scenes.**

Light scenes self-identify on the bus (their preset-recall modes), so they
were already surfaced. Shutter / "all-off" / master scenes have no such
fingerprint — they're indistinguishable from an ordinary multi-output
button — so discovery can't tell they're scenes. But the `.nkb` *does* mark
them (the Central-Function grouping). "Import Names from .nkb" now uses that:

- For every named CF group that **isn't** already a discovered light-scene,
  it finds the on-bus address that fires the group by matching the group's
  **member set** against the full routing graph (every button/IR op-point's
  linked outputs), then creates a `scene.*` entity with the group's real
  name (e.g. `ShuttersSalonCuisine`, `CloseHouse - Leave`).
- Activation **fires the trigger address** — so the modules handle roller
  run-times themselves (no HA-side timed stops), exactly like pressing the
  physical button.
- Authoritative, not heuristic: a group is imported only because the `.nkb`
  designates it a Central Function. Multi-output buttons are never promoted
  on their own, and you're never asked to classify anything.
- A group with no on-bus trigger (e.g. `ShuttersUp`/`Down` with no button)
  can't be fired from HA, so it's skipped.
- `.nkb`-sourced scenes are preserved across re-discovery (a re-scan only
  refreshes the auto-detected CFs).

After an import that creates scenes, the integration reloads so the new
`scene.*` entities appear.

## 3.4.0

`.nkb` import v2 — rooms become Areas, and scenes get their real names.

- **Rooms → Home Assistant Areas.** "Import Names from .nkb" now places each
  device in an **Area** matching its `.nkb` room (`Living`, `Cuisine`,
  `Chambre Parents`…), and the device name no longer carries the `(Room)`
  suffix — the Area provides that context. An Area you've already assigned
  by hand is never changed.
- **Scene names.** A named Central Function group in the `.nkb`
  (`Scene - Dinner`, `Scene - TV`…) is matched to a discovered CF entity by
  **member set** — the group has no bus address, but its trigger's output
  links spell out the exact `(module, channel, mode)` set discovery reads,
  so the match is unambiguous (an on-scene and an off-scene on the same
  channels stay distinct because the mode differs). The matched CF's
  device/entity is then named.
- Fixed a latent address-format bug: 16-bit **module** addresses are keyed
  as 4-hex (`0E6C`), 24-bit button/IR addresses as 6-hex (`1843B4`) — so
  module names now match (previously they'd have been missed).

## 3.3.2

- **Fix: the "Import Names from .nkb" button never appeared.** Its
  unique_id was missing from the known-entity allowlist, so the startup
  orphan-cleanup evicted it immediately after the button platform created
  it (visible as the entity flashing in, then vanishing). Added it to the
  allowlist alongside the other two bridge buttons, with a regression test
  covering all three.

## 3.3.1

- Numbered the three bridge config buttons so they show in the intended
  order (**1. Load Project Overview**, **2. Load Existing Installation**,
  **3. Import Names from .nkb**) — HA sorts them alphabetically, which
  otherwise put "Load Existing Installation" first. Display name only
  (EN/FR/NL); entity ids unchanged.

## 3.3.0

**Import device & entity names from your Nikobus `.nkb` project file.**

The Nikobus PC software stores every module / button / IR receiver under a
user-given name (with its room). A `.nkb` is a ZIP holding an MS Access
database; this release reads it directly in HA and applies those names.

- **New bridge button "Import Names from .nkb".** Put your `.nkb` export in
  the Home Assistant config directory (ideally named `nikobus.nkb`) and
  press the button. Names are applied as `Name (Room)` — e.g. the dimmer
  becomes `Dimcontroller (Centrale)`, a wall button `Entree (Living)`.
- **Non-destructive / suggested.** A device or entity you've already renamed
  by hand is never overwritten. Multi-channel modules are named at the
  device level (channels inherit it) so the same name isn't stamped onto
  every channel; single-entity devices get their entity row named too.
- **No external services.** Parsing is pure-Python (vendored Apache-2.0
  `access_parser` + the `construct` dependency); the file never leaves your
  machine.
- Scenes (Central Functions) in the `.nkb` have no bus address, so their
  names aren't auto-applied yet — that mapping is a later step.

## 3.2.1

- **Progress bar now spans 0→100 % per button.** *Load Existing
  Installation* previously opened at 30 % (the combined-pipeline weight of
  the inventory+identity phases it doesn't run); each standalone scan now
  rescales to fill the whole bar.
- Renamed the two discovery buttons to match Nikobus software terminology:
  **Discover modules & buttons → Load Project Overview** (the PC-Link
  inventory read) and **Scan all module links → Load Existing Installation**
  (reading each module's existing programming, Niko's "upload"). Updated for
  EN/FR/NL. Entity ids are unchanged.

## 3.2.0

Requires **`nikobus-connect >= 0.24.0`**.

Scene-centric Central Functions: **one scene, many triggers** — aligned to
Niko's own model (Nikobus software manual §15.6: a "Light scene / Central
function" is a single named output group activated from any number of
inputs via the `MCF` connection mode).

- **Duplicate scenes collapse.** Two buttons / IR codes wired to the
  identical outputs now surface as a **single** `scene.*` entity instead of
  two. Its `triggered_by` attribute lists **every** address that fires it
  (each as `Name (ADDRESS)`), not just one.
- **Cross-references follow every trigger.** A button/binary_sensor on any
  of a scene's trigger addresses shows the `triggers_scene` attribute, and
  the `nikobus_scene_activated` event fires no matter which trigger is
  pressed (the event's `address` is the one actually seen on the bus).
- An on-scene and its separate off-trigger stay distinct (their member
  modes differ), and per-key scenes with different members still split.
- ⚠️ On the first discovery after upgrade, a scene that previously appeared
  under a non-canonical trigger address may move to its canonical
  (sorted-first) trigger address — its `unique_id`/entity id changes once.
  Re-point any automation/dashboard that referenced the old entity.

## 3.1.0

Scene presentation & cross-references (HA-side only, no new dependency).

- **Scenes cross-link with their trigger.** A CF / light scene now exposes
  a `triggered_by` attribute — the wall button / IR code that fires it,
  as `Name (ADDRESS)` — and the triggering button / binary_sensor exposes
  a `triggers_scene` attribute. You can find one from the other at a glance.
- **Human-readable attributes.** Scene members and button "linked outputs"
  now show the module's friendly name with the address in brackets
  (e.g. `dimmer_module_d1 (0E6C)`) plus the level, instead of bare
  addresses.
- **New event `nikobus_scene_activated`** fires whenever a scene's trigger
  address is seen on the bus (physical press *or* HA activation), carrying
  the scene's `address` / `name` / `entity_id` / `member_count` — so
  automations can react to a *scene* firing, not just a raw button press.
- Scenes remain standard `scene.*` entities — activate with `scene.turn_on`.

## 3.0.1

Requires **`nikobus-connect >= 0.23.0`**.

- **Light-scene CFs now surface one scene per trigger / IR code**, keyed on
  the address that actually fires it (e.g. IR `30A` → `9E4E2C`, `30B` →
  `DE4E2C`). Previously every preset/light-scene IR code on a receiver
  collapsed into one mega-scene keyed on the receiver base (e.g. `0D1C80`),
  whose activation frame the bus ignored — so those scenes did nothing from
  HA. Each scene is now individually activatable via `scene.turn_on`
  (including scenes with no physical trigger button). 38xx PC-Logic
  broadcast CFs are unaffected.
- ⚠️ On the first discovery after upgrade the affected CF scene
  `unique_id`s change (receiver-base → per-code wire form), so the old
  merged `scene.nikobus_*` entity is replaced by the per-code ones —
  re-point any automation/dashboard that referenced it.

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
