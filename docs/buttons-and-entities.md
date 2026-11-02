# Buttons, inputs & the entity model

## The model: device → op-points → (button + sensor)

Every physical button is one **device**. Each *operation point* on it — a key on a keypad, an IR code on a receiver, an A/B state on an input — gets **two entities**:

| Entity | Direction | Use |
|---|---|---|
| **Button** | HA → bus | Press it to emit the same bus frame a physical press would (simulate the press). |
| **Binary sensor** *(disabled by default)* | bus → HA | Pulses to `pressed` (then back to `idle` after ~1 s) when the real button is pressed; use it in state-based automations. |

> Binary sensors are **disabled by default** — enable them on the device page if you want to monitor presses.

## Choosing which press entities exist

Most installations run on their outputs and the feedback from the modules; the press entities are there for the few keys you want to press or watch from Home Assistant. *Configure → Choose which press buttons and press sensors exist* lists six classes and creates press buttons and press sensors only for the ones you tick:

| Class | What it covers |
|---|---|
| Wall buttons | Bus push buttons of every series, and the keys of IR receivers |
| Interfaces | Switch, push-button and universal interfaces (`05-056` / `05-057` / `05-058`) |
| Remotes and IR codes | RF transmitters, clustered remotes, and the IR codes learned by receivers |
| Input modules | The A / B keys of PC-Logic and Modular Interface inputs |
| Audio triggers | The keys an Audio Distribution module listens for, Power included |
| Virtual buttons | Buttons that exist only in programming: the logic outputs of a PC-Logic, and the virtual input banks the Nikobus software creates for programs and scenes — inventoried from the output modules' link tables |

An unticked class gets no entities **and no devices**: what it had is removed on the reload that follows, so the device list shows only what you use. Nothing else changes — `nikobus_button_pressed` and the other [events](events.md) fire for every key, `controlled_by` and shutter travel times still come from the modules, the A/B latch switches of input modules stay, and `nikobus.send_button_press` works on any address.

A fresh installation starts with nothing ticked; an installation upgraded from a release before 3.22.0 keeps every class ticked, so nothing disappears on upgrade. Ticking a class off and on again loses nothing either: before an entity or device is removed, its entity id, name, icon and area are kept in the button store and put back when it exists again.

## PC-Logic & Modular Interface inputs

The PC-Logic (`05-201`) and Modular Interface (`05-206`) each expose **6 inputs**. Each input is rendered as its own child device under the owning module:

- PC-Logic inputs → **`LM-INPUT 1`…`LM-INPUT 6`** (LM = Logic Module).
- Modular Interface inputs → **`MI-INPUT 1`…`MI-INPUT 6`** (MI = Modular Interface).

Each input has two keys — **A** and **B** — because the firmware emits one telegram when the contact closes (A) and another when it releases (B). Each key has the usual button + binary-sensor pair:

- Use the **A / B buttons** to simulate the input going on/off *from* HA.
- Use the **A / B sensors** to monitor the input's real state on the bus.
- Use the **A/B latch switch** (added in 3.0.0) for a persistent on/off mirror: the **A** signal latches it on, **B** latches it off, and `turn_on` / `turn_off` drive the matching bus frame. It tracks physical presses and other controllers, and survives restarts.

> The latch switch assumes the input emits **both** its A and B telegrams (one per direction) — the normal case. An input wired to emit only one of the two can be turned on/off from HA but its mirrored state won't follow physical presses in both directions.

Input addresses are computed by firmware from the module's own address (not stored in inventory) and synthesised automatically — no configuration needed.

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.lm_input_1_key_a
    to: "pressed"   # these sensors report "pressed" / "idle"
```

## What each button exposes

After a link scan, button entities carry their wiring as attributes:

```yaml
linked_outputs:
  - module_address: "0E6C"
    channel: 1
    mode: "M01 (Dim on/off (2 buttons))"
wall_button_address: "0D1C80"
wall_button_model: "05-348"
wall_button_type: "IR Button with 4 Operation Points"
wall_button_key: "1C"
```

Conversely, every light / switch / cover exposes a **`controlled_by`** attribute listing the buttons that drive it — so you can answer "which wall button turns on this light?" from the entity page.

An output can also be driven by one of the PC-Link's **calendar channels** (CH001 … CH100, the virtual buttons that calendar programs and scenes fire). Those links appear in `controlled_by` as `Calendar channel CH001A (PC-Link)`, and each channel gets a device *PC-Link calendar CH001A* under the bridge. It has no press entity: what the PC-Link puts on the bus when it fires a channel is not known, so Home Assistant never tries to emit it.

## Renaming

Discovered devices get generic names like `Bus push button, 4 control buttons (1843B4)`. You have two ways to give them friendly names:

- **In bulk, from your project** — [import them from the `.nkb`](nkb-import.md). This is the fastest way to name a whole install at once, rooms and all — plates get the same numbering as the Nikobus software (`7: Porte buanderie (Buanderie)`), their keys become `Porte buanderie Key 1A`, and the names **persist across restarts and re-discovery**.
- **By hand** — rename in the HA UI (*device → ⋮ → Rename*). HA stores this as `name_by_user` and **preserves it across reloads, restarts, and re-discovery** (names are keyed by the entity's stable `unique_id`).

The two coexist: a non-overwrite `.nkb` import only supplies the *default* name, so anything you've renamed by hand is left untouched — and a manual rename you later remove falls back to the imported name, not the generic one.

## IR codes across multiple receivers

When the same IR code is learned by several receivers, each gets its own op-point with a distinct bus address, and the name is qualified with the receiver (`IR 30A on 0D1C80`) so duplicates stay distinguishable.

## Virtual / off-bus buttons

Discovery inventories the buttons that exist only in programming from the output modules' link tables: a PC-Logic **logic output** (the frame the PC-Logic puts on the bus when a logic function fires; the module's own memory lists them in order, so each is shown as *PC-Logic 8DC8 L01* under the PC-Logic's device, numbered as the Nikobus software numbers the logic functions) and the keys of the software's **virtual input banks** (*Virtual button 3CF006*, under *Virtual buttons*). Their links appear in `controlled_by` like any key's, a logic output's frame is a known press that refreshes the outputs it drives, and with the *Virtual buttons* class ticked they get press entities. Addresses outside those classes that are not on the physical bus (IR scene triggers, hand-added codes) aren't created as entities. Fire them from scripts:

```yaml
service: nikobus.send_button_press
data:
  address: "84DFFC"
```

---

## Audio Distribution zones

A 05-205 keeps its links in its own memory, and discovery now reads them: for each zone, the bus address that switches it on or off, steps the volume, and selects each source. Every zone becomes a **media player** with those controls, and the trigger addresses are listed in its attributes.

The module answers no state query, so a zone's state is what was last commanded. It is not guesswork on one side only: a wall key pressing the same function is relayed by the PC-Link, so pressing *Zone 2 on* at the wall updates the entity too.

Each address the module listens for is also a press button and press sensor of its own — *Zone 2 Source 3*, *Audio Power* for the module's own Power object — filed under the module's device rather than under a wall plate, since no plate owns them. They belong to the *Audio triggers* class of [press entities](#choosing-which-press-entities-exist), so they exist only if that class is ticked; the media players do not depend on it.

## Motion detectors

A motion detector with Nikobus interface (`05-7*5` / `430-00500`) sends four button frames: its output switching **on** (motion while the light level is below its threshold, or its own button) and **off** (the run time has elapsed), and the light level at the sensor going **above** or **below** its threshold. The threshold (2–1000 lux) and the run time (5 s – 30 min) are set with the potentiometers on the sensor; the bus carries no lux value and no timing.

Each detector gets two binary sensors on its device:

| Sensor | Device class | On | Off |
|---|---|---|---|
| *Occupancy* | occupancy | the output switched on | the output switched off |
| *Light level* | light | above the threshold | below the threshold |

The light level is the total light at the sensor, so switching on the room light counts as much as daylight. Occupancy follows the detector's output rather than each movement: it stays on for the run time. The detector cannot be asked for its state; the sensors keep their last state across restarts and stay *unknown* on a new install until the first frame. They exist whichever [press-entity classes](#choosing-which-press-entities-exist) are ticked.

The four frames are also the detector's keys 1A (on), 1B (off), 1C (light) and 1D (dark), with the press entities and `nikobus_button_pressed` events of any key. A detector inventoried before 3.31.0 has only 1A and 1B; run **1. Load Project Overview** once to add the other two. The two sensors work either way.

## RGB controller

A 340-00112 LED / RGB controller becomes one **light** on its own device. The controller answers the ordinary state query with an on flag and one lit-or-not flag per colour output — never a level — so the light shows on or off and, in its attributes, the colour the lit outputs mix to (`colour: magenta`, `red`, `green`, `blue`). It is polled like the output modules and re-read after a press on a key that drives it.

It accepts no command of its own: the bus offers no way to set its output, and every one of its fifteen modes is a key behaviour (the two-key luminance mode, the four-key colour-and-luminance mode, the scenario start/stop modes, a one-key toggle, …). Turning the light on or off therefore **presses a linked key** — the key whose role fits best: the on or off key of a two-key mode, a toggle, the start/stop key of a colour scenario, a preset. Nothing is pressed when the light already is what you asked for. Colour and brightness cannot be set from Home Assistant.

Which keys are linked to the controller is known only from your **`.nkb` project file**: the controller's link table cannot be read from the bus, not by this integration and not by the Nikobus software, which only status-polls it and writes a project file with its settings but no link. The vendor software's own plugin for this family confirms it: it marks the link table as write-only, and what it writes there — 128 records of 18 bytes — is documented in [`documentation/rgb-controller-memory-map.md`](../documentation/rgb-controller-memory-map.md), together with the write protocol the software uses, taken from its executable. [Import your `.nkb`](nkb-import.md) and the keys it holds are applied — the light lists them in `linked_keys`, with the key it will press for on and off in `on_key` / `off_key`, and the plate's key shows the controller in `controlled_by`. Without an import, or with no key of a usable role, the light is a readout and switching it raises an error that says so.
