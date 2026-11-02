# How the integration works

The code is split into two packages.

**[nikobus-connect](https://github.com/fdebrus/nikobus-connect)** — pip-installed, pinned in `manifest.json`. The low-level layer:

- `NikobusConnect` — serial/TCP transport, PC-Link handshake and the presence probe.
- `NikobusEventListener` — parses CR-terminated ASCII frames, validates both checksums (the PC-Link's CRC-8 and the module's CRC-16), dispatches presses and feedback.
- `nikobus_connect.press` — infers a press from its `#N` frames: wire-time duration, hold milestones, burst-tolerant release.
- `NikobusCommandHandler` — queued, retrying command processor that owns the bus lock every exchange takes, merges set-output requests per module group, and ignores a state answer that arrives before its own acknowledgement (a Feedback Module's, not ours).
- `NikobusAPI` — high-level operations (read/set output state, cover start/stop, key press as one back-to-back burst, module status / checksum / memory image, PC-Link clock).
- `NikobusDiscovery` — PC-Link inventory + module register scan; reads the PC-Link's registry header to bound the sweep and filter its diagnostic filler pages, reverse-engineers button→output mappings, decodes the Audio Distribution module's own link table, recognises the PC-Link's calendar channels, and classifies CF broadcasts.

**This integration (`custom_components/nikobus/`)** — the Home Assistant glue:

- `coordinator.py` — wires the library together; owns polling, discovery lifecycle, state signals, and the `.nkb` name/Area/scene apply.
- `nkbstorage.py` — the three HA Stores (`nikobus.modules`, `nikobus.buttons`, `nikobus.cfs`).
- `nkbsnapshot.py` — keeps the entity ids, names, icons and areas of press entities and devices that a class change removes, and puts them back when the class returns.
- `nkbnames.py` — reads names, rooms, per-channel names, and scene groups from a `.nkb` project (Access database in a ZIP, parsed with a vendored pure-Python reader).
- `nkbmanual.py` — optional fallback import of `nikobus_*_config.json` for no-PC-Link installs (inventory source only).
- `nkbprogramming.py` — the read-only maintenance layer: module status and checksum checks, memory backups, the reprogramming watch, the PC-Link clock.
- `nkbreconcile.py` — decides which central functions become scenes; the store helpers it used to hold (controlled-by, button status, member sets) are the library's `discovery.store` now.
- `discovery_mixin.py` — the discovery lifecycle on the HA side (inventory, link scan, the residue probe and its Repairs issues, `.nkb` import); the progress arithmetic and the post-scan reconciliation are the library's.
- `nkbactuator.py` — feeds button frames to the library's press tracker, fires the `nikobus_button_*` events and signals, and reads the modules a key drives.
- `nkbconfig.py` — scene-file loader/writer.
- `nkbtravelcalculator.py` — the library's cover travel model on the integration's clock.
- `devices.py` — registers the HA devices: one per wall button, input module, opaque module, and the synthesized children (PC-Logic inputs, remote codes, PC-Link calendar channels, audio triggers under their module) — for the press-entity classes that are ticked.
- `router.py` — maps module channels to HA entity types, decides the press-entity class and the parent device of every op point, and builds the `controlled_by` reverse index.
- `config_flow.py` — config flow (connection → hardware → polling) and the Configure options menu (customize, upload `.nkb`, import `.nkb`, scenes, press entities).
- `repairs.py` — the "No Nikobus buttons configured" repair flow.
- `diagnostics.py` — the diagnostics download for bug reports.
- `entity.py` + the platforms (`light` / `switch` / `cover` / `fan` / `button` / `binary_sensor` / `sensor` / `scene`).

## Staying in sync

1. **Button-driven refresh** — each button carries its `linked_modules`; a press, seen on the bus or sent by Home Assistant, refreshes the impacted module group(s) about a second later.
2. **Periodic refresh** — the polling interval when there is no Feedback Module, or when the Feedback Module option is off.
3. **Feedback Module push** — a 05-207 polls, on its own, the output modules it is configured to track, and the PC-Link relays both its queries and the modules' answers. The integration captures those answers and updates the entities without sending anything itself, so the periodic poll is switched off when the option is set. The output group of an answer is known from the query that preceded it, which is why this mode needs the PC-Link's port: the Feedback Module's own port relays answers only. The module sends one query about every 33 s, walking its own list: on a ten-module installation some groups came up every 2 minutes and the groups with LED keys every 12 to 30 minutes, often as two queries 1.4 s apart, so the five-minute safety poll reads those groups more often than the module does.

## Interoperability

The integration talks to Nikobus hardware over its serial bus. It was developed independently, solely for interoperability between Home Assistant and Nikobus hardware the user already owns, in line with Article 6 of Directive 2009/24/EC. The `.nkb` reader parses a project file the user already owns, locally, for the same purpose.
