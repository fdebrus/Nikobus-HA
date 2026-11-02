# Discovery workflow

Everything is driven from the **Nikobus Bridge** device page. It carries six action buttons and five diagnostic sensors:

| Entity | Role |
|---|---|
| **1. Load Project Overview** | Enumerate modules and buttons from the PC-Link |
| **2. Load Existing Installation** | Read every output module's link table; extract scenes |
| **3. Import Names from .nkb** | Apply names, rooms and scenes from the project file |
| **Backup module programming** | Read every module's memory image into a backup folder |
| **Verify module programming** | Check status, family and checksum of every module |
| **Sync PC-Link clock** | Set the PC-Link clock from Home Assistant (the only write) |
| **Connection** | `connected` / `reconnecting` / `disconnected`, with the gateway's address and type |
| **Discovery status** / **Discovery progress** | Phase and percentage of a running scan |
| **PC-Link clock** | The controller's time, read hourly, with `drift_seconds` |
| **Programming health** | `ok` / `problem` / `unknown`, per-module detail in the attributes |

The three numbered buttons are meant to be pressed **in order**, top to bottom.

> One bus action at a time: while a scan or a maintenance run is in progress, **every bridge button greys out** and re-enables when it finishes. Polls, user commands and scans share one bus lock, so they cannot garble each other; there is still no point starting a scan in the seconds after a reload, while the start-up sync is reading the modules.

## 1. Load Project Overview

Press **1. Load Project Overview**. The integration probes the PC-Link, walks its inventory, and creates a device for every module and every physical button on the bus.

Two diagnostic sensors track progress:
- **Discovery status** — its state is the coarse phase (`idle` / `pc_link` / `module_scan` / `finished` / `error`), so history stays clean; the live per-register line is in the `message` attribute, alongside the fine-grained detail (all kept out of the recorder).
- **Discovery progress** — 0–100 %.

## 2. Load Existing Installation

Press **2. Load Existing Installation**. This reads each output module's link table and records which button drives which channel (the `linked_modules` metadata). It also extracts **Central Function (CF)** scenes found in those tables. The button is disabled until an inventory has run.

## 3. Import Names from .nkb *(optional)*

Press **3. Import Names from .nkb** to apply the friendly names, rooms, and scenes from your project file in one shot. The button **replays whatever you last applied** through the *choose-what-to-import* form; before that form has ever been used, it imports **everything, non-destructively**. See [Importing from your `.nkb` project](nkb-import.md) for the file upload step, the form, and what the imported names look like.

## Customize a module *(optional)*

*Configure → Customize a module* lets you set the **module type**, for a module the inventory reported as the wrong family, and per channel:
- **Description** → the entity name.
- **Entity type** → how HA exposes it (switch modules: `switch`/`light`; dimmers: `light`/`fan`; rollers: `cover`/`switch`/`light`; any of them `disabled` for no entity). A dimmer output driving a variable-speed extractor fan shown as `fan` gets a speed slider (0–100 % over the channel's 0–255 range) instead of a brightness, and HomeKit exposes it as a fan.
- **LED on / off key** → the bus address of the wall key whose LED shows this channel. When filled, Home Assistant *presses that key* instead of switching the output, so the key's LED and the output stay in step (a key that toggles the output itself, in impulse mode, must be driven this way). When empty, Home Assistant switches the output directly, and a plate LED catches up only when the Feedback Module next polls that module (see [Commands on the bus](commands-on-the-bus.md)).
- **Travel time up / down** (rollers) → seconds to open/close, used by the position calculator. Leave them alone and the cover runs on the operating time programmed into the module's own roller links — the time it really keeps the relay engaged, per channel and per direction. The form shows that value, so saving it makes it explicit; a value you type wins over the links. Only a channel whose module has no roller link falls back to 30 s.
- **End-stop margin** (rollers) → seconds after the estimated arrival at fully open/closed before a Home Assistant-started motion sends its stop frame (default 3). The motor runs into the end stop during the margin, which keeps the position model honest; the stop then releases the relay so a wall button acts on the first press. Set it higher to let the module's own run time release the relay instead.

Closing the dialog reloads the integration, so a new entity type, travel time or scene is there at once. Changes persist in `.storage/nikobus.modules` and survive re-discovery, except the module type: **1. Load Project Overview** writes back what the PC-Link reports, so a type override has to be set again after each inventory.
