# Installs without a PC-Link

When **1. Load Project Overview** gets no inventory from a PC-Link — there is none on the bus, Home Assistant is connected through a Feedback Module's or PC-Logic's port (these answer the probe but hold no project), or the Nikobus software never stored the project in the PC-Link — the inventory comes from two files in `/config` instead:

| File | Holds |
|---|---|
| `nikobus_module_config.json` | Your output modules (switch, dimmer, roller) and their channels |
| `nikobus_button_config.json` | Your wall buttons and their keys |

Step 1 loads them; **2. Load Existing Installation** then reads which key drives which output from the modules themselves, exactly as on an install with a PC-Link.

**Connected to the Feedback Module's port:** leave the **Feedback Module** option off and let Home Assistant poll — see [Which port is Home Assistant connected to?](connectivity.md#which-port-is-home-assistant-connected-to) for why.

## The quick way: let your `.nkb` write them

If neither file exists yet and your Nikobus project export is in `/config` — named `nikobus.nkb`, or the only `.nkb` there — step 1 writes both files from it and loads them. Open them afterwards and check two things: the shutter run times are a placeholder (40 s), and channels the project left unnamed are called `Output 1`, `Output 2`, …. Edit what you need, then press step 1 again. Without a project file, write the files by hand as shown below.

## How the files are used

- **Only step 1 reads them**, and only when no PC-Link inventory comes of it. They are never read at startup.
- **They replace the lists entirely.** A module or button that is not in the file disappears from Home Assistant on the next step 1, and anything changed under *Customize a module* is overwritten by what the file says. Keep the file as your reference: put run times, LED keys and entity types in it rather than only in the dialog.
- **Links are not in the files.** Which key drives which output is read from the modules by step 2; never write it yourself.
- **After every edit:** press **1. Load Project Overview**, then **2. Load Existing Installation**.
- **JSON rules:** straight double quotes (`"`, not the curly `“ ”` a forum or word processor produces), no comma after the last item, and addresses as quoted hexadecimal strings (`"4707"`, not `4707`). The log says so when a file cannot be read.
- **A file with no readable module changes nothing.** If not a single module can be read from `nikobus_module_config.json` (an empty list, the wrong layout), the module store is left as it is and the log says why, rather than every module disappearing. Duplicate addresses and unknown module types are named in the log too.
- **A copy of the `.storage` file works.** A file shaped like `.storage/nikobus.modules` (one `nikobus_module` group, each entry with its own `module_type`) is read with each module's own type.

## `nikobus_module_config.json`

One list per module type — `switch_module`, `dimmer_module`, `roller_module` — and one entry per module, with one channel entry per output, in output order:

```json
{
  "switch_module": [
    {
      "address": "4707",
      "description": "Switch module kitchen",
      "model": "05-000-02",
      "channels": [
        {"description": "Kitchen ceiling"},
        {"description": "Kitchen island", "entity_type": "light"},
        {"description": "Extractor socket"},
        {"description": "not_in_use output_4"},
        {"description": "Garden lights", "led_on": "8B7086", "led_off": "8B7086"},
        {"description": "Output 6"},
        {"description": "Output 7"},
        {"description": "Output 8"},
        {"description": "Output 9"},
        {"description": "Output 10"},
        {"description": "Output 11"},
        {"description": "Output 12"}
      ]
    }
  ],
  "dimmer_module": [
    {
      "address": "0E6C",
      "description": "Dimmer living",
      "model": "05-008-02",
      "channels": [
        {"description": "Living ceiling"},
        {"description": "Living uplighter"},
        {"description": "Bathroom fan", "entity_type": "fan"},
        {"description": "Output 4"}
      ]
    }
  ],
  "roller_module": [
    {
      "address": "9105",
      "description": "Shutters ground floor",
      "model": "05-001-02",
      "channels": [
        {"description": "Shutter WC", "operation_time_up": "30.5", "operation_time_down": "32", "end_stop_margin": "8"},
        {"description": "Shutter kitchen", "operation_time": "45"},
        {"description": "Output 3"},
        {"description": "Output 4"},
        {"description": "Output 5"},
        {"description": "Output 6"}
      ]
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `address` | yes | The module's 4-digit hexadecimal address, as in the Nikobus software or on the module's label. |
| `description` | no | The module's name in Home Assistant. |
| `model` | no | Shown on the device page. |
| `channels` | yes | One entry per output, in order; see the counts below. |
| `channels[].description` | no | The output's name. A name starting with `not_in_use` creates no entity for that output. |
| `channels[].entity_type` | no | What the output becomes. Switch module: `switch` (default) or `light`. Dimmer: `light` (default) or `fan`. Roller: `cover` (default), `switch` or `light`. `disabled` creates no entity. |
| `channels[].led_on` / `led_off` | no | The bus address of the key whose LED shows this output; Home Assistant then presses that key instead of switching the output (see [Commands on the bus](commands-on-the-bus.md)). Leave out, or `""`, for none. |
| `channels[].operation_time_up` / `operation_time_down` | rollers | Seconds to open / to close, used to compute the position. `operation_time` alone sets both. Avoid exactly `30`: older versions wrote it everywhere, so it is read as "not set" and the run time programmed in the module is used instead — write `30.5` or `31`. |
| `channels[].end_stop_margin` | rollers | Seconds the motor keeps running after the estimated arrival at fully open / closed (default 3). |

How many channel entries each module needs:

| Module | Type | Channels |
|---|---|---|
| 05-000-02 | `switch_module` | 12 |
| 05-002-02 | `switch_module` | 4 |
| 05-007-02 | `dimmer_module` | 12 |
| 05-008-02 | `dimmer_module` | 4 |
| 05-001-02 | `roller_module` | 6 |

## `nikobus_button_config.json`

One list, `nikobus_button`, with **one entry per key**. Keys of the same button carry the same `linked_button` address and become one device with one entity per key:

```json
{
  "nikobus_button": [
    {"address": "8B7086", "description": "Hall left top", "linked_button": [{"address": "1843B4", "key": "1A", "channels": 4, "type": "Push button", "model": "05-064-02"}]},
    {"address": "CB7086", "description": "Hall left bottom", "linked_button": [{"address": "1843B4", "key": "1B", "channels": 4, "type": "Push button", "model": "05-064-02"}]},
    {"address": "0B7086", "description": "Hall right top", "linked_button": [{"address": "1843B4", "key": "1C", "channels": 4, "type": "Push button", "model": "05-064-02"}]},
    {"address": "4B7086", "description": "Hall right bottom", "linked_button": [{"address": "1843B4", "key": "1D", "channels": 4, "type": "Push button", "model": "05-064-02"}]},
    {"address": "2D1C80", "description": "Garage door contact"}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `address` | yes | The key's bus address: the 6-digit code the key puts on the bus when pressed. |
| `description` | no | The key's name in Home Assistant. |
| `linked_button[0].address` | for multi-key buttons | The button's own address (the same for all its keys), as the Nikobus software shows it for the button. |
| `linked_button[0].key` | with it | Which key: `1A`, `1B` on a 2-key button; `1A`–`1D` on a 4-key; `1A`–`1D` and `2A`–`2D` on an 8-key. |
| `linked_button[0].channels` | with it | How many keys the button has: `2`, `4` or `8`. |
| `linked_button[0].type` / `model` | no | Shown on the device page. |

**Finding the addresses.**
- **A key's bus address:** open *Developer tools → Events*, enter `nikobus_button_pressed` under *Listen to events*, start listening and press the key: the event's `address` is the key's bus address. It works for keys the integration does not know yet. Do this for every key.
- **The button's own address** (`linked_button`): from the Nikobus software, where each button is listed with its address. On a 4-key button the keys' bus addresses differ only in their first digit, which helps to check that you grouped the right keys.
- **Without the button's own address**, list each key with only `address` and `description` (as the garage contact above). Each key becomes a device of its own instead of being grouped with the other keys of its button, and otherwise works the same: its presses fire the `nikobus_button_*` events, and step 2 attaches its links by the key's address (since 3.30.4), so `controlled_by` is filled and a press refreshes the outputs it drives.
- **Entries named `DISCOVERED …` are skipped** unless they carry `linked_button`. Files written by releases before 3.0 list every key they ever saw that way, real keys included; give those keys a name of your own (or any name not starting with `DISCOVERED`), and leave out the bus noise those files also collected — single addresses such as `000000`, `DFFFFF` or `FFFE01` that share their last five digits with no other key.

> **With a PC-Link holding your project** these files are not needed: step 1 reads the inventory from the PC-Link and never consults them, and names live in Home Assistant (see [Renaming](buttons-and-entities.md#renaming)). The integration logs a warning when it finds them; you can delete them.
