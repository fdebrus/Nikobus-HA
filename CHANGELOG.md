# Changelog

## 3.16.1

- **Feedback module read works through link mode.** On a real 05-207 the status query is answered but block reads are ignored, so 3.16.0's Backup and LED import stopped at the first block. `nikobus-connect 0.36.1` (required) retries the read in link mode, the mode the module is programmed in, and leaves it again in every case. Link mode changes no programming.


## 3.16.0

- **New: Import LED links from feedback module.** A bridge button (and `nikobus.import_feedback_leds` action, with an *Overwrite* option) reads the programming of the feedback module (05-207) and fills the LED-on / LED-off trigger address of every output channel with the wall key whose feedback LED tracks it — the addresses you had to look up and type by hand under *Customize a module*. The key is identified from the plate the feedback module names and from the links discovery already knows; a report of the assignments is returned by the action. Typed addresses stay unless Overwrite is on; imported ones are refreshed on every run. Requires `nikobus-connect >= 0.36.0`.
- **Backup and Verify include the feedback module.** Its image lands in the backup as `<address>_feedback_module.nkm`; the checksum comparison is skipped for it because its coverage is not known yet, and a missing status reply is tolerated.
- **Fix: cover run times from the module links never matched the stored links.** The lookup expected a flat link shape while discovery stores links as per-module output lists, so every cover fell back to the configured or default time. Now read from the stored shape.
- **Fix: the PC-Link clock reply appeared as a phantom module** (`F5FF`, carrying the date as its output state) on installs with a feedback module. Fixed in `nikobus-connect 0.36.0`.


## 3.15.6

- **Fix: discovery scans failed on every module since 3.15.0.** The count-driven scan asks each module for its status first. The module's reply is a `$18` frame, and during the module stage the integration still treated any `$18` frame as "a module button was pressed, discover it" — using the byte-swapped wire address. Each real scan therefore spawned a phantom scan of a module that does not exist, whose status query held the command queue for 15 s while the real scan's register reads timed out one after another. Status replies are now ignored in the module stage; the engine already consumes them from its response queue.
- Ruff findings in the 3.15.5 tests (import order, unused variable).


## 3.15.5

- **Covers: the stop frame at fully open/closed is back, sent after an end-stop margin.** Since 3.14.0 a motion started from Home Assistant that ended at 0 or 100 sent no stop at all and left the relay to the module's own run time. Where that run time is longer than the real travel (a common installer setting) the relay stayed engaged well after the shutter had arrived, and a wall button pressed in that window only stopped the relay — a second press was needed to move the shutter. The stop frame is now sent **End-stop margin** seconds after the estimated arrival (new per-channel setting under *Customize a module*, default 3 s, same as before 3.14.0): the motor still runs into the end stop during the margin, which keeps erasing the position drift, and the relay is released right after, so the wall button acts on the first press. A large margin restores the 3.14.0 behaviour for installs that prefer it.
- The cover's `end_stop_margin` is exposed as a state attribute next to the travel times.


## 3.15.4

- **All bridge buttons grey out while any bridge action runs** — discovery, a programming check or a backup — one bus action at a time, and no more "referenced entity not available" surprises from pressing a second one.
- **Home Assistant 2027.8 / 2027.9 deprecations resolved.** Device parents are now registered with `via_device_id`, device lookups use `async_get_device_by_identifier`, and the orphan cleanup enumerates the config entry's devices instead of the registry mapping — the "deprecated `via_device` parameter" / "uses `device_registry.devices` as a mapping" warnings (24 per start-up) are gone.


## 3.15.3

- **Fix: dimmer modules no longer fail the programming check.** A dimmer's own checksum skips the six bytes between its two link banks; the check computed it over the whole image, so every healthy dimmer was flagged with a false "checksum mismatch" Repair issue. Fixed in `nikobus-connect 0.35.1` (required). Re-run **Verify module programming** once to clear the issue.
- The programming report shows *no second table* as empty instead of 255 on switch and roller modules.


## 3.15.2

- **Fix: "Listener loop failed: 'NikobusDiscovery' object has no attribute 'process_mode_button_press'"** logged on every module-status reply. Status frames arriving outside a discovery run were routed to a discovery method that no longer exists; they are now ignored there (the query layer already consumes them). The backup / verify results were unaffected, only the log was.
- **Sync PC-Link clock stays available during a backup or check.** It only needs two queued commands, so it no longer greys out (and no longer triggers "referenced entity not available" when pressed) while a longer maintenance run is in progress. Discovery still blocks it.
- The PC-Link clock sensor reads the clock as soon as it is added instead of waiting for the first hourly poll (it stayed "unknown" for an hour after every restart).
- Bus frames are logged as "Bus event frame" instead of "Press frame" at debug level.


## 3.15.1

- **Fix: the 3.15.0 buttons and sensors never appeared.** The new hub entities (Sync PC-Link clock, Verify / Backup module programming, PC-Link clock, Programming health) were created at startup and immediately deleted by the integration's orphan cleanup, which only keeps entities whose ids it knows. They are now registered as known; after updating they show up on the Nikobus Bridge device without any further action.


## 3.15.0

- **New: Backup module programming.** A bridge button (and `nikobus.backup_modules` action) reads the complete programming image of every switch, dimmer and roller module — the button links, timers and hash tables the module runs on — into `config/nikobus_backup/<timestamp>/` as one `.nkm` file per module plus a `summary.json`. A lifeline for installs whose Nikobus PC software and project file are long gone. Read-only on the bus.
- **New: Verify module programming.** Asks every output module for its own status (EEPROM-error flag, number of links) and checks the checksum it computes over its memory against the image read back. Results land on the new **Programming health** diagnostic sensor (per-module detail in its attributes) and any faulty module raises a Repair issue naming it. Also available as `nikobus.verify_modules`.
- **New: PC-Link clock.** A diagnostic timestamp sensor shows the controller's own clock (re-read hourly, drift against Home Assistant in an attribute) and a **Sync PC-Link clock** button / `nikobus.sync_pc_link_clock` action sets it from Home Assistant's local time — the controller's calendar functions never knew about daylight-saving changes until now.
- **Covers use the run time programmed into the module.** When a roller channel still carries the discovery placeholder (30 s), its travel time now comes from the roller links programmed into that module (the time the module itself keeps the relay engaged), so the position model and the 3.14.0 end-stop behaviour match the hardware. A value you set on the channel yourself still wins.
- Discovery reads exactly the links each module reports instead of a fixed band, and decodes dimmer ramp times — via `nikobus-connect >= 0.35.0` (required).


## 3.14.0

- **Covers self-recalibrate on every full open/close.** The position
  model dead-reckons from configured operation times, so the estimate
  slowly drifts from the physical shutter (motor ageing, temperature,
  tick rounding). Previously a stop frame was sent the moment the
  *estimate* said the travel was done — freezing that drift in place,
  and `set position 0/100` stopped with no margin at all. Now any
  HA-commanded motion ending at 0 or 100 sends no stop frame: the motor
  runs into its mechanical end stop (erasing the drift), and the roller
  module's own per-channel run time releases the relay — exactly what
  already happens after a physical wall-button full travel.
  Intermediate positions and explicit Stop are unchanged.

## 3.13.2

- **Fix: the A/B latch switch of Modular Interface inputs stayed frozen
  even after 3.13.1** (#485 follow-up). 3.13.1 fixed the input
  *addresses*, and the A/B press sensors started pulsing — but the
  latch switch computed its own addresses instead of reading them from
  storage, still using the old Logic-Module formula. It now uses the
  exact same addresses the sensors listen on (and falls back to a
  type-aware derivation only for malformed entries), so it latches on
  the A signal and clears on the B signal as designed. Its on/off
  commands also transmit the correct addresses now. Logic-Module
  latches were unaffected.

## 3.13.1

- **Fix: Modular Interface (05-206) inputs never updated their A/B
  sensors and latch switch** (#485). The 05-206 computes its input bus
  addresses with a different firmware scheme than the Logic Module
  (05-201), but discovery applied the Logic-Module formula to both —
  so the integration listened on addresses the hardware never emits.
  Hardware captures from the reporter's install pinned the real 05-206
  scheme (and re-validated the 05-201 one on a third unit). Requires
  `nikobus-connect >= 0.34.0`. After updating, press **1. Load Project
  Overview** once — the corrected input entries replace the stale ones
  automatically.
- CI: skip the HACS license check — the repository's PolyForm
  Noncommercial license is real but not in GitHub's detection dataset
  (which contains no noncommercial license at all), so the check can
  never pass by design.

## 3.13.0

- **Key and IR child devices now inherit their plate's Area.** Every key
  of a plate is its own HA device, and none of them ever got an Area
  from the `.nkb` import — on a real install that left ~150 of ~215
  Nikobus devices invisible to Area views and the auto-generated
  dashboard. The Areas import now propagates the plate's room to all
  its children (IR op-points included). Manually-assigned Areas are
  preserved unless Overwrite is ticked.
- **Press-simulation buttons and press sensors are now *diagnostic*
  entities.** They're automation signals and tools, not day-to-day room
  controls — the lights, covers, and switches are. On device pages they
  move to the collapsed Diagnostic section, and auto-generated
  dashboards stop listing ~150 of them as controls. Automations,
  scripts, and manual dashboard cards that use them are unaffected.
- **New import category: Labels.** The `.nkb` import can now apply
  entity-class labels — `Nikobus Output`, `Nikobus Button`,
  `Nikobus Scene` — for one-click filtering in HA's tables and target
  pickers. Additive only: your own labels are never touched, and
  removing one of ours sticks until you re-run the labels import.

## 3.12.0

- **Button plates get the Nikobus application's numbering straight from
  the bus.** The PC-Link registry records carry the same component index
  the Niko software shows (`BP7`), and discovery now reads it (requires
  `nikobus-connect >= 0.33.0`). Plates without an imported name are
  labelled `7: Bus push button, … (1843B4)` instead of the bare generic
  name, so the HA device list cross-references with the Nikobus
  application even on installs without an `.nkb` project file. An
  imported `.nkb` name still takes precedence, and PC-Links that don't
  expose the registry header keep the plain names.
- **Fix: `.nkb` name import now survives restarts without Overwrite.** The
  default (non-destructive) import wrote names into the device registry's
  integration-owned field, which every restart overwrote with the generated
  defaults — the imported names silently disappeared unless the buried
  Overwrite toggle was used. Imported names are now persisted in the
  integration's own storage and re-asserted on every start, so the
  prominent "3. Import Names from .nkb" bridge button finally sticks. This
  also puts the imported name on the device page ("Device info" card), not
  just on entities. Overwrite is back to meaning only "also replace names
  I set myself".
- **Bridge buttons grey out while a scan is running.** All three bridge
  action buttons (inventory discovery, module scan, `.nkb` import) now
  show as unavailable for the duration of a discovery scan — no more
  wondering whether the press registered, no more accidental
  double-triggers (the double-press race window is also closed
  coordinator-side).
- **The two import paths now behave identically.** The
  Configure → "Import from .nkb" form remembers your last-applied
  choices (categories + Overwrite) and pre-fills them on the next visit,
  and the bridge button replays those same settings instead of always
  running its own defaults.

## 3.11.0

- **Discovery: 05-061 button plates (2 buttons with feedback LEDs) are now
  recognised.** PC-Link registry device type `0x05` sat unidentified for
  years, so these plates were silently dropped from every inventory. A full
  discovery log plus the matching `.nkb` pinned the type to the 05-061 —
  three registry records matched the install's 05-061 components on both
  bus address and plate index.
- **Discovery: no more phantom buttons or "Unknown device type 14/24/34"
  warnings from PC-Link filler pages.** The PC-Link's registry starts with
  a header page (a diagnostic byte-ramp ending in a magic marker plus the
  record count), and reads past the last record wrap back into ramp pages.
  The scan now reads the header's record count to bound the sweep and
  skips ramp filler outright — previously a ramp page decoded as a phantom
  05-060 button at a nonsense address and later ramps fired spurious
  unknown-device warnings. Units that don't expose the header page behave
  exactly as before.
- **`.nkb` import: button plates now carry the same index as the Nikobus
  application.** The Niko PC software labels each plate `BP7: Porte
  buanderie`; the import now applies the index too (`7: Porte buanderie
  (Room)` — the locale-specific `BP` prefix is dropped, the number is the
  data), so the HA device list cross-references one-to-one with the Nikobus
  application. Modules keep their plain names.
- **`.nkb` import: unnamed plates fall back to their room.** A plate the
  installer never named (empty label in the Nikobus software) previously
  kept its generic bus-address name; it now gets its room name so the
  device stays identifiable.
- **`.nkb` import: per-key devices renamed from the plate.** Each key of a
  wall plate is its own HA device, previously stuck with the generated
  `Push button 1A #N9A43A2` label. Once the parent plate is matched to its
  `.nkb` name, its keys become `Porte buanderie Key 1A` etc. (the raw bus
  address stays available in the entity attributes). IR op-points,
  PC-Logic input keys and your own renames are never touched.
- As always, import names are **suggested defaults** — any name you set
  yourself wins, unless you explicitly run the import in overwrite mode.
  Requires `nikobus-connect >= 0.32.0`.

## 3.10.3

- **Fix: spurious "Unknown device detected" warnings from corrupted discovery
  frames.** Discovery/inventory frames (`$18`/`$2E`/`$1E`) were forwarded to
  the device classifier without a CRC check, unlike every other frame class
  the library handles. A bit error on the wire — cable, connector, USB-serial
  adapter noise — could sail through and be logged as a new "unknown device
  type" (sometimes seeding a phantom module/button), even though the bus
  data itself was fine. Requires `nikobus-connect >= 0.30.2`.
- **Fix: `.nkb` import can now enable output channels the bus scan left
  hidden, not just rename ones that already have an entity.** The register
  scan reads link records, never channel names — Nikobus modules don't store
  per-channel text on the bus — so every output channel starts as the
  internal `"not_in_use output_N"` placeholder, and no entity is created for
  it. Previously the `.nkb` import could only **rename an entity that
  already existed**, so a channel with none stayed hidden forever with no
  way out except manually using **"Customize a module."** The import now
  writes the `.nkb`'s real output name straight into the channel (when no
  entity exists for it yet) and reloads, so the entity is created directly.
  A user's own "Customize a module" changes (including explicitly disabled
  channels) are never touched. The import button's log line now reports how
  many outputs were newly enabled.

## 3.10.2

- **Fix: wrong bus addresses on `.nkb`-bootstrapped multi-key buttons.** The
  per-key addresses generated in 3.10.1 didn't match what the bus actually
  emits on a press, so the keys appeared but automations/press-events never
  fired. They're now derived exactly as a PC-Link inventory would
  (`convert_nikobus_address` + key offset). Requires
  `nikobus-connect >= 0.30.1`. Re-run **"Bestaande installatie laden"** after
  updating to regenerate the button file with the correct addresses.

## 3.10.1

- **`.nkb` bootstrap: multi-key wall plates are expanded to all their keys.**
  A 2/4/8-button plate now becomes a single button with an op-point per key
  (`1A`/`1B`/`1C`/`1D`…) instead of collapsing to `1A`. Without this the
  module scan dropped every non-`A` key (a 4-button kept only one key's
  links; many 2-button plates came back empty). Requires
  `nikobus-connect >= 0.30.0`.

## 3.10.0

- **Bootstrap from a `.nkb` when there's no PC-Link (and no config files).**
  Discovery now has a third inventory fallback: after (1) probing for a
  PC-Link and (2) reading `nikobus_module_config.json` /
  `nikobus_button_config.json`, it will (3) generate those two files from a
  Nikobus **`.nkb`** project export dropped in your config dir. Every module
  (address, model, channel names) and every button (address, name) is read
  straight from the `.nkb` and written to disk **as a backup**, then loaded
  normally. Existing config files are never overwritten. Roller run-times
  default to `40` (the `.nkb` doesn't store the per-shutter value) and
  button→output links still come from **"Scan all modules"**. Requires
  `nikobus-connect >= 0.29.0`.

## 3.9.3

- **Fix: named scenes disappeared after a module re-scan (regression in
  3.9.2).** A plain module re-scan overwrites the stored Central Functions
  with the freshly discovered ones, which carry no name — the `.nkb` names
  live only on the stored records. Since 3.9.2 stopped surfacing *unnamed*
  button-driven scenes, that name wipe silently dropped every named scene on
  the next re-scan. Re-scan now **carries the `.nkb` names over** to the
  matching CF (by address, then by member set), so named scenes survive a
  re-scan. Re-importing the `.nkb` also now reloads immediately, so scenes
  reappear without a restart. **If your scenes vanished: re-import the
  `.nkb` once to restore the names.**

## 3.9.2

- **Unnamed "phantom" scenes are no longer surfaced.** A button-driven
  light-scene (fired by a real wall button or IR input, e.g. addresses such
  as `829201` / `AA2481` / `84DFFC`) is only surfaced as a Home Assistant
  scene once the `.nkb` import has matched and **named** it. An unnamed one
  is just a button you already have on the bus — surfacing it as a nameless
  scene only duplicated that button. Named scenes from your Nikobus project
  (e.g. *Scene - TV*, *CloseHouse - Leave*) are unaffected and still appear,
  and the bare `38xx` central functions always appear. Any phantom scene
  entities a previous version created are cleaned up on the next
  scan / restart. A plain module re-scan is enough to apply this.

## 3.9.1

- **Fix: output state stuck after a physical button press (issue #469).**
  An output toggled from Home Assistant and then changed at the physical
  wall button could stay frozen on the Home-Assistant value — the module
  was read correctly (e.g. `000000000000`), but the entity never updated,
  on the button-driven refresh *or* the poll. The write-diff cache that
  skips redundant re-renders only tracked coordinator-driven writes, so an
  optimistic write (turn on/off, button-operation) left it stale; a later
  update that rendered the same value as the stale cache was wrongly
  suppressed. The cache now refreshes on every state write regardless of
  source, so the corrected state always lands. Most visible on serial /
  PC-Link installs without a feedback module (polling mode).

## 3.9.0

- **Roller central functions are now grouped covers, not scenes.** A
  Nikobus Central Function whose members are *all* roller (shutter)
  channels — including `M01` "open-stop-close" toggle groups — is now
  surfaced as a single member-driving **cover** (open / close / stop)
  instead of a broadcast or directional scene. The cover drives every
  member channel atomically through the per-module commit path (one bus
  frame per module) with a timed stop from the channels' operation times,
  so it is deterministic even for `M01` toggle groups, which a broadcast
  could not be. These covers live under a new **"Central functions"**
  device category. Mixed (light + roller) and light-only CFs are
  unchanged — they stay scenes/broadcasts.

## 3.8.8

- **Scene `outputs` attribute now shows channel names.** Each member of a
  CF scene's `outputs` attribute shows its channel as `Name (N)` — the
  output channel's imported / user name with the channel number in
  parentheses, e.g. `Boudoir - Plafonnier (8)` — so you can read what a
  scene drives without mapping channel numbers by hand. The name reflects
  the `.nkb` channel-name import and any manual rename; channels with no
  name keep the bare number.

## 3.8.7

- **Fix: `nikobus_scene_activated` event carried `name: null`.** Since
  3.8.5 the scene's name lives on its device (the entity name is `None`
  to avoid a doubled friendly name), so the event payload now reads the
  scene name from the device — automations matching on the scene name
  work again.

## 3.8.6

- **`.nkb` name import now renames the scene's own device.** 3.8.5 moved
  CF scenes onto their own ``cf_<address>`` device, but HA never repaints
  a device's name once it exists — so a scene whose name was matched on a
  *later* import (e.g. *Scene - TV* / *Dinner* / *CosiDinner*) kept its
  generic ``Nikobus scene <addr>`` device name even though the name was
  correctly stored on the CF. The import's device-rename pass now
  recognises the ``cf_<addr>`` scene devices and force-applies the CF /
  scene name (taking the scene name, never the trigger button's). Re-run
  **Import Names from .nkb** once and the matched scenes pick up their
  real names. (CFs created directly from the ``.nkb``, like
  *CloseHouse - Leave*, were already correct because their device is
  created with the name in place.)

## 3.8.5

- **Central Function scenes now get their own device, named after the
  scene.** A CF scene whose bus address is also a physical button (e.g.
  *CloseHouse - Leave*, triggered by a *Corridor Master DOWN* wall button)
  was being merged into that button's HA device and shown under the
  button's name — the scene was buried as a sub-entity. CF scenes now
  register a distinct ``cf_<address>`` device under the Scenes hub, named
  by the CF (the directional roller Open/Close scenes share one device per
  CF). The physical button keeps its own device. Entity IDs are unchanged,
  so dashboards and history referencing the scene survive. The matched
  ``.nkb`` scene name is also persisted onto the CF record so the scene
  device keeps the right name on its own device.

## 3.8.4

- **Roller central functions are now actionable from HA.** An imported
  roller central function (a *roller_pair* CF) used to appear as a one-shot
  scene that couldn't move anything: a single broadcast carries both the
  open and close links for its channels, so there was no "direction" to
  trigger. These now surface as **member-driving scenes** that fire the
  shutters directly through the atomic, per-module bus commit — every
  member channel on a module moves **in one frame** (all-at-once, like the
  native Nikobus scene), with a timed stop from the channels' run times:
  - a **2-button** (open+close) function becomes **two scenes** — one
    "… Open" and one "… Close";
  - a **single-direction** function (close-only / open-only) becomes one
    scene for that direction.

  1-button "open-stop-close" (M01) toggles and the other CF patterns
  (light scenes, switch pairs) keep their existing single-broadcast
  activation.

## 3.8.2

Robustness, i18n & cleanup pass — a few user-facing fixes, otherwise internal.

- **Friendlier errors when a command can't reach the bus.** Turning a
  switch / light / cover on or off — or activating a scene — now surfaces a
  clean, translated "communication failed" message when the bus is
  unreachable, instead of the raw library exception. The optimistic UI
  state still rolls back on failure.
- **Complete French & Dutch translations.** Filled in 29 strings that were
  English-only (error messages, the reconfigure form, and the diagnostic
  service descriptions), corrected a stale service field, and added the
  missing translations for the *Send button press* action.
- **Icons for the last bridge button and the services.** The *Import Names
  from .nkb* button and the three inventory services now show their own
  icons instead of the generic fallback glyph.
- **Lighter button handling.** A latch-switch toggle or a simulated button
  press no longer wakes *every* entity on the bus to filter itself out;
  only the affected addresses are notified.
- **Internal tidy — no behaviour or configuration changes.** Removed a few
  unreachable code paths, de-duplicated the storage wrappers and the
  command-error helper, modernised imports / typing across the platforms
  and helpers, refreshed stale in-code / README references, and tidied the
  test suite (closed leaked event loops, strengthened a few weak tests).
  The full test suite stays green.
- **Type-checks against the `nikobus-connect` library directly.** The
  library now ships a `py.typed` marker (0.25.0), so the integration's
  use of its API is type-checked for real — the `ignore_missing_imports`
  override for it has been dropped and the dependency pinned to
  `nikobus-connect>=0.25.0`.

## 3.8.1

- **Fix: don't leak the bus connection when setup fails partway.** If the
  connection opened but a later setup step failed, the bus was left open;
  because only one client may hold the bus, every retry then failed. Setup
  now tears the connection back down before retrying.
- **Cleaner unload.** Platforms are unloaded before the connection stack is
  stopped, and the coordinator is only stopped if the unload succeeded.
- Setup failures now surface translated messages instead of raw text.

## 3.8.0

Performance & logging pass — no behaviour or configuration changes.

- **Per-address wakeups.** A button press used to wake *every* output/button
  entity on a shared bus event, each filtering itself out by address — O(N)
  per press. Presses (and per-module poll refreshes) are now routed by
  address so only the impacted module's / button's entities are notified.
  On a large install that's a handful of callbacks per press instead of one
  per entity.
- **Skip redundant state writes.** Switch, light and cover now diff their
  resolved state before writing, so an unchanged poll cycle is a cheap
  comparison instead of a full re-render. Availability changes and real
  state changes still write.
- **Quieter polling.** A module is only re-broadcast to its entities when its
  bytes actually changed; the coordinator's own post-poll refresh still
  covers everything.
- **Cleaner discovery history.** The discovery-status sensor's state is now
  the coarse phase (`idle` / `pc_link` / `module_scan` / `finished` /
  `error`); the live per-register line moved to a `message` attribute and the
  volatile detail is kept out of the recorder.
- **Standardised log messages** across the integration (one consistent style;
  levels unchanged).

## 3.7.0

- **Import per-channel output names.** The `.nkb` import now also reads the
  name of each output you actually toggle — the light / cover / switch
  behind a channel (e.g. `Appliques Salon`, `Terrasse`) — and applies it to
  the matching entity, not just the module/button device names.
- **Choose what to import.** Settings → Devices & Services → Nikobus →
  **Configure → Import Names from .nkb** is now a form: tick which
  categories to apply — **device names**, **channel names**, **Areas**,
  **scenes** — so you can, say, import channel names without touching the
  Areas you've already organised.
- **Overwrite toggle.** Off by default (suggested names only, a manual
  rename always wins). Turn it on to force the `.nkb` names / Areas onto
  entries you've previously set yourself.
- The **Import Names from .nkb** button stays the one-press path: it imports
  everything, non-destructively.

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
