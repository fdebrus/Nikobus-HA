# Troubleshooting

## Repair issues at a glance

Every condition the integration can diagnose is a Repair issue (*Settings → System → Repairs*) that names the cause and clears itself once the cause is gone.

| Repair issue | Meaning | What to do |
|---|---|---|
| No Nikobus buttons configured | Discovery has never run | Press *1.* then *2.* on the Bridge device |
| *N* button(s) flagged as legacy | Buttons with no decodable links, or links only to modules that are gone | Review them: remove the residue, and keep the ones meant to have no links (HA-only keys, unwired interfaces) so later scans stop flagging them |
| Module needs reprogramming | A module's link table read as corrupt and was skipped | Reprogram it with the Nikobus PC software, rescan |
| Module reports an EEPROM error | The module flags its own memory | Reprogram it with the Nikobus PC software |
| Programming checksum mismatch | The image read back differs from the module's checksum | Re-run *Verify*; if it persists, reprogram |
| Module was reprogrammed | Its checksum moved since its links were last read (a dimmer's power-loss memory alone does not count) | Run *2. Load Existing Installation* |
| Module does not answer | A stored module has never answered a state read while the others do; it is now read once an hour | If it no longer exists, remove it with `nikobus.purge_stale_inventory` and from your Nikobus project; otherwise check its power and wiring |
| Module is not a *type* | It answers as another family than the inventory says | Run *1. Load Project Overview*; correct the type under *Customize a module* if it stays wrong (and again after each later inventory, which writes the reported type back) |
| No Nikobus device answered on *port* | Nothing answered the presence probe | Cable, PC-Link power, other software on the port; clears on the first frame |
| Connected to the Feedback Module's port | Pushed answers arrive without their queries | Turn the Feedback Module option off |

## Discovery says "No PC-Link" although I have one (PC-Logic installs)

If your install has a **PC-Logic (05-201) configured as master**, the PC-Logic answers the bus address-inquiry, not the PC-Link. The integration (like Niko's own software) reads the device inventory **only from a PC-Link**. Make sure Home Assistant's connection terminates at the **PC-Link**, then re-run discovery. A PC-Logic that answers instead is reported as "connect to a PC-Link module" — the same message the Nikobus PC software shows.

## Buttons missing, or a button shows the wrong number of control points

Run **1. Load Project Overview** followed by **2. Load Existing Installation** while connected to the PC-Link. If a specific physical button type isn't recognised at all, its device-type byte may be uncatalogued — open an issue with the diagnostics download and the model number printed on the button.

## A two-button plate's presses are not recognised

The two-button plates `05-060` (and `4*-072`), `05-061` and `05-060-02` come in two versions that report the same type and model on the bus but send their two keys on different addresses: either where a four-button plate has its left pair, or where it has its right pair. The integration tells them apart per plate from the links the output modules hold for it, during **2. Load Existing Installation**, and remembers the result; both versions can be mixed in one installation. The `4*-082` always uses the right pair.

A plate whose keys are linked to no output module cannot be told apart and gets the left-pair addresses. If its presses arrive with `module_address: null` or as *Press from unknown button*, compare the `address` a press shows under *Developer tools → Events* (`nikobus_button_pressed`) with the key's entity attributes, and open an issue with both: the plate's version can then be set from its address.

## The `.nkb` import did nothing / "no .nkb file found"

- Make sure the file is uploaded: *Configure → Upload .nkb project file* (it's saved as `nikobus.nkb`), or place it in `/config` yourself as `nikobus.nkb`.
- If several `*.nkb` files are in `/config` and none is named `nikobus.nkb`, the integration won't guess — rename the one to import to `nikobus.nkb`.
- Names/Areas you'd already set by hand are **left alone** unless you tick **Overwrite** in *Import from .nkb (choose what)*. If a non-overwrite run "did nothing", it likely had nothing new to fill in.
- Remember the **3. Import Names from .nkb** button replays the *last-applied* form settings — if you previously imported only, say, Areas, the button repeats that selection until you change it in the form.
- A file that won't parse (corrupt / not a real export) is reported as an error and nothing is changed.

## Imported names disappeared after a restart

Fixed in **3.12.0**. Up to 3.11.0 a non-overwrite import wrote names into a registry field that Home Assistant resets from the integration's defaults on every start, so the imported names silently reverted (only the Overwrite path survived). Since 3.12.0 imported names are stored by the integration itself and re-applied on every start. If you're on an older version: upgrade, then re-run the import once.

## Entities show as `Manual button`, or some entities are missing after an upgrade

This is **stale data** left in storage from an older release (e.g. an install that previously fell back to manual config). Symptoms: entries typed `Manual button`, and HA logging *"Platform nikobus does not generate unique IDs … already exists"* (duplicate addresses cause real entities to be dropped).

To clear it:

1. **Stop Home Assistant completely** (not just reload — `.storage` is rewritten on shutdown, so deleting while running won't stick).
2. Delete `.storage/nikobus.buttons` and `.storage/nikobus.modules`.
   *(Keep any `nikobus_*_config.json` files if you rely on them for inventory.)*
3. Start HA, then run **1. Load Project Overview** → **2. Load Existing Installation**.

The store rebuilds cleanly with proper device types and no duplicates. Names you set in the HA UI are keyed by `unique_id` and reattach automatically.

**Coming from 0.6.x / 0.7.x:** those releases used other unique_ids. Since 3.30.2 the first start re-keys the old entities, so they keep their entity id, name and area. Update straight to the current version; an install that already upgraded through an earlier 3.x lost the old entries.

## Custom channel/button names didn't carry over

Friendly names you set in Home Assistant live in HA's entity/device registry (keyed by `unique_id`) — a clean re-discovery preserves them as long as the bus addresses are unchanged. Names applied by a `.nkb` import are stored by the integration (since **3.12.0**) and re-applied automatically. (Removed in **3.0.0**: names are no longer imported from a legacy `nikobus_button_config.json`; set them in HA, or [import them from your `.nkb`](nkb-import.md), and they persist.)

## *2. Load Existing Installation* left a module without links, or logged "link table looks corrupt"

Up to 3.17.0 a poll already in flight when the scan started could be sent in the middle of a register read; the two replies garbled each other and the module's link table was discarded as corrupt, leaving every button on it with empty `linked_modules`. Update to 3.17.1 or later, where bus exchanges are serialised, and run the scan again. If a module still reads as corrupt on 3.17.1+, the Nikobus PC software will report the same: reprogram that module with it.

## Repair issue: "Nikobus module … was reprogrammed"

The module's checksum no longer matches the one recorded when its links were last read, so its programming changed — usually the Nikobus PC software. Run **2. Load Existing Installation** (or *Verify* / *Backup*); the issue clears once the module is read again. See [Maintenance](maintenance.md).

## Setup fails with "Could not exclusively lock port"

The integration opens the serial port exclusively, so nothing else may use it: another integration, an add-on, or a leftover `platform: serial` sensor on the same device (`/dev/ttyUSB0`, …). Remove the other user of the port and restart Home Assistant.

## Repair issue: "No Nikobus device answered on …"

The port opened but nothing answered the presence probe. Check the cable and the PC-Link power, make sure the Nikobus PC software is not holding the port, and if the PC-Link was used by another program, power-cycle it. The issue withdraws itself as soon as any Nikobus frame is received, so if the bus works it is gone within seconds. See [Connectivity](connectivity.md).

## Repair issue: "Home Assistant is connected to the Feedback Module's port"

Your serial cable is on the Feedback Module, not on the PC-Link, and the Feedback Module option is on. Turn the option off (*Configure → Change hardware settings*); state is then polled, which is the only correct mode on that port. See [Which port is Home Assistant connected to?](connectivity.md#which-port-is-home-assistant-connected-to).

## A press from Home Assistant switches the lights on and immediately off, or does nothing

The key is linked in **impulse mode** (M05): every press toggles. Before 3.18.2 the repeats of a press left the PC-Link 150 ms apart and an impulse link could count them as two presses. Update, keep *Simulated press repeats* at its default, and if the channel's LED must follow, keep the key's address in its **LED on / off key** fields so Home Assistant drives it by pressing the key. Since 3.30.0 a relay light or switch driven that way is read before and after the press, so `turn_on` / `turn_off` give a defined end state. For a `button.press` on an impulse key, check the entity a few seconds after the press and press again only if needed — the pattern under [Commands on the bus](commands-on-the-bus.md).

## The plate LED does not follow a light I switch from Home Assistant

Plate LEDs are driven by the Feedback Module (05-207), not by the plate: without one they stay dark. When it sees a key's telegram on the bus it toggles the LEDs of every key that drives the same output, on every plate, straight away, and keeps that LED state itself. An output switched any other way — directly from Home Assistant, by a timer, by a PC-Logic program — does not reach that state: an output switched *on* is picked up when the Feedback Module next reads the module's group, 12 to 30 minutes on the installation tested; an output switched *off* is not: the LED stayed on through three reads over 40 minutes, and an inverted LED took hours to clear overnight. A short press that the module applied but the Feedback Module missed (two frames, right after a motion detector's burst) leaves the LED inverted, and further presses keep it inverted; to realign it, set the output to what the LED shows (from Home Assistant), then press the key once. To keep a channel's LED in step, put the key's bus address in its **LED on / off key** fields under *Customize a module*: Home Assistant then presses the key instead of switching the output, after reading the module so a press that is not needed is never sent. The key must drive that output only; the dialog refuses a key linked to other outputs too, since pressing it would switch them along (seen on an installation with a 05-207 and LED plates, October and November 2026).

## Relays click several times when I switch several lights of one module

Fixed in 3.18.3: requests for the same module group made together are merged into one frame. Update, and remove any delays you had put between the service calls.

## State is slow to update

Without a Feedback Module, external changes (a wall press on a key Home Assistant does not know, another client) are only seen on the next poll. Physical button presses and presses sent by Home Assistant both trigger a read of the impacted modules within about a second. Lower the polling interval, or add a 05-207 Feedback Module: its own polling is then picked up, though a change without a press can still take a few minutes to show.

With a Feedback Module, the module itself polls the output modules it is configured to track and the integration listens to the answers; with debug logging you see each one as `Feedback frame for module 4707 group 2 — outputs …`. If those lines never appear, the Feedback Module is not tracking that module: add the output to its configuration in the Nikobus PC software and download the module again. An output the Feedback Module does not track is not lost either: every five minutes the integration reads the module groups (channels 1–6, 7–12) it has heard nothing from in that long (a safety poll, logged at debug level as `Feedback-Module safety poll`), and only those, so a tracked group costs nothing on the bus. To read one module right now, call `homeassistant.update_entity` on one of its entities, which reads that entity's group whatever the mode, or [`nikobus.refresh_module`](services.md#nikobusrefresh_module). If the lines appear but the wrong lamps change, read the Repair issue about the Feedback Module's port above.

## Stale records from a previous install

A second-hand PC-Link (or replaced hardware) can leave records for modules that are no longer on the bus. Run [`nikobus.detect_stale_inventory`](services.md#nikobusdetect_stale_inventory) to see which addresses don't respond, then [`nikobus.purge_stale_inventory`](services.md#nikobuspurge_stale_inventory) to remove the ones you confirm are gone. See [Services](services.md) for all eight services and their parameters.
