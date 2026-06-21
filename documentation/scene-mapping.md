# Nikobus Central Functions → Home Assistant mapping

This is the design spec for how the integration should surface Nikobus
*Central Functions* ("Light scenes" / groups) as Home Assistant entities.
It is derived from the official Nikobus software manual in this folder
(`PHNikobus_EN.pdf`, software v3.0). Page references below point there.

## 1. A Nikobus "scene" is a *group*, not an HA scene

> *"Using the **Light scene / Central functions** command, it is possible
> to place several alliances [outputs] in a **group** … This programming …
> can be different for every output that is part of the group."* — p.162

So a Central Function (CF) is an **output group** where **each member
channel carries its own mode**. The group is fired by linking it to an
input through connection mode **MCF** ("Activate Light scene / Central
function", p.164).

When the group is created you choose its **type of operation —
1-button, 2-button or 4-button** — and *"Depending on this decision, only
certain modes will be available."* (p.163). This is the `1 Knops / 2 Knops
/ 4 Knops` choice in the mode dialog.

## 2. Determinism = button-count **and** mode

The button-count decides how many bus commands the group has, and the
member mode decides what each does:

| Operation | Bus commands | Behavior |
|---|---|---|
| **1-button, single-action mode** (`open`, `close`, `on`, `off`, `atmosphere on`, `preset on`) | one address | **deterministic one-shot** — fires the defined action |
| **1-button, on/off mode** (`atmosphere on/off`, `dim on/off`) | one address | **toggle** — alternates each press (p.595 "alternately switch on and off") |
| **2-button / 4-button** (any) | **two (or more) addresses** — an *activate* and a *deactivate* | **two-state / deterministic per button** (p.30: upper = open, lower = close, either-while-moving = stop) |

Key point: a **2-button group is fired by a *pair* of inputs** — one for
the "on/open/activate" half and one for the "off/close/deactivate" half.
It is **not** a single one-shot and **not** a toggle.

## 3. Mode reference (per module type)

From the mode tables on p.110–111, with the resulting effect:

### Shutter module (rolluikmodule)
| Mode | Meaning | Buttons | Effect |
|---|---|---|---|
| `M02` | Open | 1 | deterministic open (re-press = stop) |
| `M03` | Close | 1 | deterministic close (re-press = stop) |
| `M04` | Stop | 1 | stop |
| `M06`/`M07` | Open/Close with control time | 1 | timed open/close |
| `M01` | Open – stop – close | **2** | up = open, down = close, stop |
| `M05` | RF and interface | **4** | RF-triggered — **not on the wired bus** |

### Dimmer (dimcontroller)
| Mode | Meaning | Buttons | Effect |
|---|---|---|---|
| `M04` | Atmosphere on (Sfeer aan) | 1 | deterministic scene recall |
| `M12` | Preset on | 1 | deterministic preset level |
| `M05`/`M06` | On / Off | 1 | deterministic on / off |
| `M13` | Dim on/off | 1 | toggle |
| `M01` | Dim on/off | **2** | up = brighter/on, down = dimmer/off |
| `M02` | Dim on/off | **4** | separate on/off/up/down |
| `M03` | Atmosphere on/off (Sfeer aan/uit) | **4** | scene on **and** off |
| `M11` | Preset on/off | **4** | preset on **and** off |

### Switch module (schakelmodule)
| Mode | Meaning | Buttons | Effect |
|---|---|---|---|
| `M02` | On with operating time | 1 | deterministic on |
| `M03` | Off with operating time | 1 | deterministic off |
| `M04` | Push | 1 | on while pressed |
| `M05` | Pulse (teleruptor) | 1 | toggle |
| `M14` | Atmosphere on | 1 | deterministic scene recall |
| `M01` | On / off | **2** | up = on, down = off |
| `M13` | Step switch | **2** | step |
| `M15` | Atmosphere on/off | **2** | scene on **and** off |

## 4. How a 2-button group behaves, by content

A 2-button group has an *activate* input and a *deactivate* input
(two bus addresses); each member still runs its own mode:

| Group contents | Activate | Deactivate | Net |
|---|---|---|---|
| **All covers** | every shutter opens | every shutter closes (re-press = stop) | deterministic open/close/stop |
| **All lights** | lights → on / preset | lights → off | deterministic on/off |
| **Mixed** | full "on" state (lights to preset + shutters open) | "off" state (lights off + shutters close) | two-state scene |

## 5. Mapping rules → HA entity

The faithful mirror routes each group to the HA primitive that matches its
behavior — **not everything is an HA "scene".**

| Nikobus group | HA entity | Notes |
|---|---|---|
| **1-button, scene/preset recall** (`atmosphere on` `M04`/`M14`, `preset on` `M12`) | **scene** | one-shot state recall — already correct today |
| **1-button / broadcast deterministic** group of `M02`/`M03`/on/off members (e.g. a close-all) | **scene** | broadcasting the address executes the defined action deterministically — correct today |
| **Shutter group** (`M01`/`M02`/`M03` roller members) | **cover** (open/close/stop), member-driving | the faithful "group" representation; deterministic regardless of `M01` toggle |
| **2-button light group** (`atmosphere on/off`, on/off) | **two scenes** (On + Off) or a light/switch **group** | a single HA scene loses the "off" half |
| **2-button mixed group** | **two scenes** (Activate + Deactivate) | |
| **`M05` RF group** (e.g. OpenHouse) | — | not bus-discoverable; comes only from the `.nkb` if member links exist |

## 6. Current state vs target

**Correct today**
- Per-channel roller **covers** (open/close/stop) — every shutter is individually deterministic.
- **Scene/preset** CFs (`M04`/`M12`) → broadcast scene (deterministic recall).
- CFs with deterministic `M02`/`M03`/on-off members fired by broadcast (e.g. *CloseHouse - Leave* closes everything) → correct.
- `roller_pair` (`3880xx`) CFs **with `M02`+`M03` members** → directional Open/Close member-driving scenes.
- User software scenes from `nikobus_scene_config.json` → member-driving, deterministic.

**Gaps to close**
1. **`M01` shutter *groups* are broadcast toggles** (e.g. *ShuttersSalonCuisine*, 9105 ch2–5 `M01`). Faithful but non-deterministic in HA. → make them **member-driving grouped covers** (open/close/stop), which works for `M01` too because it commands the output state, not the link.
2. **The member-driving path is gated on `pattern == "roller_pair"` (address `3880xx`) only.** It should key on **"has roller members"** so `nkb_scene` / `light_scene` roller groups are covered too.
3. **2-button light/switch groups** (`M15` / `M01` on-off) → broadcast toggle; could be a deterministic on/off pair.

## 7. Caveats

- **`M05` RF groups** (OpenHouse / CloseHouse-Sleep): triggered by an RF
  wall transmitter (`PhysicalAddress = 0x3FFFFF`). They carry **no output
  link records** in the project, so they cannot be reconstructed from the
  bus or the `.nkb` — out of scope until Niko exposes the membership.
- **Determining direction for a 2-button group** needs knowing which of a
  CF's trigger addresses is the *open/on* half vs the *close/off* half. For
  a member-driving cover this is sidestepped (we drive the channel state
  directly); for a broadcast on/off pair it must be resolved from the link
  records.
- **Mixed groups** (lights + a shutter, e.g. `829201`) stay **scenes** —
  their intent includes a light-state recall.

## 8. References

`documentation/PHNikobus_EN.pdf` (Nikobus software manual v3.0):
- p.30 — §5.11, the 2-button garage-door example (up / close / stop)
- p.110–111 — shutter, dimmer and switch mode tables
- p.162–165 — §15.6 *Light scene / Central functions* = output group,
  1/2/4-button operation, MCF activation
