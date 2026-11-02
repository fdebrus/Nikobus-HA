# Services

The integration registers eight services in the `nikobus.` domain. All appear in **Developer Tools → Actions** with a form (from `services.yaml`) and can be called from scripts and automations.

## `nikobus.query_module_inventory`

Triggers a discovery scan. It has three modes depending on which fields you pass:

- **Full inventory** — no fields: re-enumerate every module and button from the PC-Link, exactly like the bridge's *1. Load Project Overview* button (without a PC-Link it loads the manual config files instead).
- **Single module** — `module_address` only: re-scan one module's link table, like *2. Load Existing Installation* does for every module.
- **Forensic register read** — `module_address` + `register_start` + `register_end` (+ optional `sub_byte`): read a specific register range, for reverse-engineering / diagnostics.

| Field | Required | Example | Description |
|---|---|---|---|
| `module_address` | no | `"05"` | Module to scan. Omit for a full PC-Link inventory. |
| `register_start` | forensic | `"0x70"` | First register (forensic mode). |
| `register_end` | forensic | `"0x83"` | Last register (forensic mode). |
| `sub_byte` | no | `"04"` | Memory bank — `04` output link table (default), `00` module header, `01` BP-cell / vendor map. |

Forensic mode requires `module_address`, `register_start` **and** `register_end` together. The action returns as soon as the scan has started; the discovery progress sensor follows it, and results go to the integration log and the stores. Can't run while another discovery is in progress.

```yaml
action: nikobus.query_module_inventory
data:
  module_address: "05"
```

## `nikobus.send_button_press`

Fires a button-press frame on the bus for `address` — exactly as if that physical key or IR code were pressed. Handy for triggering a Central Function / light scene, or a virtual / off-bus address that discovery doesn't know about, from an automation.

| Field | Required | Example | Description |
|---|---|---|---|
| `address` | **yes** | `"84DFFC"` | 6-hex bus address to broadcast. **Quote it** — the YAML scientific-notation trap. |

No response value.

```yaml
action: nikobus.send_button_press
data:
  address: "84DFFC"
```

## `nikobus.refresh_module`

Reads one output module's state from the bus now: one group, or both. For an output nothing else refreshes: one a Feedback Module does not track, or one switched by a PC-Logic logic function the integration cannot attribute to a press. `homeassistant.update_entity` on one of the module's entities does the same for that entity's group.

| Field | Required | Example | Description |
|---|---|---|---|
| `address` | **yes** | `"81F6"` | Address of a switch, dimmer, roller or RGB module. Not a button. |
| `group` | no | `1` | `1` for channels 1–6, `2` for channels 7–12. Omitted: both. |

No response value. An address that is not a known output module is a validation error.

```yaml
action: nikobus.refresh_module
data:
  address: "81F6"
  group: 1
```

## `nikobus.detect_stale_inventory`

Probes every known output module for **bus presence** and returns which respond and which don't — useful after replacing hardware, or with a second-hand PC-Link that still carries the previous install's records. **Read-only**: it never modifies storage (pair it with `purge_stale_inventory`).

| Field | Required | Default | Description |
|---|---|---|---|
| `outer_attempts` | no | `1` | Full-sweep passes (1–3); raise on a busy bus. |
| `outer_delay` | no | `0` | Seconds between passes (0–10). |

**Returns** (response data): `checked`, `present_modules`, `absent_modules`, and `orphaned_buttons` (buttons whose links all point at absent modules).

```yaml
action: nikobus.detect_stale_inventory
data:
  outer_attempts: 2
  outer_delay: 3
response_variable: stale
```

## `nikobus.purge_stale_inventory`

Removes the given module addresses (and any buttons orphaned by them) from the persisted module + button stores. Feed it the `absent_modules` from `detect_stale_inventory` once you've confirmed they're really gone.

| Field | Required | Example | Description |
|---|---|---|---|
| `addresses` | **yes** | `["CCDD", "EEFF"]` | Module addresses to remove. |

**Returns** (response data): `removed_modules`, `removed_buttons`, `not_found`.

```yaml
action: nikobus.purge_stale_inventory
data:
  addresses: ["CCDD", "EEFF"]
response_variable: purged
```

## `nikobus.backup_modules`

Reads every output module's programming image (or only `addresses`) into `config/nikobus_backup/<timestamp>/` and returns the folder path, the image file names and the per-module status / checksum summary. Read-only on the bus. Same as the **Backup module programming** button.

| Field | Required | Example | Description |
|---|---|---|---|
| `addresses` | no | `["9105", "4707"]` | Module addresses to back up. Omit for every output module. |

## `nikobus.verify_modules`

Asks every output module (or only `addresses`) for its status and checksum, reads its memory back and compares. Returns the health verdict and per-module detail; the same data drives the **Programming health** sensor and the EEPROM / checksum Repair issues. Same as the **Verify module programming** button.

| Field | Required | Example | Description |
|---|---|---|---|
| `addresses` | no | `["9105"]` | Module addresses to verify. Omit for every output module. |

## `nikobus.sync_pc_link_clock`

Sets the PC-Link clock to Home Assistant's local time and returns the time written. No fields. The only action that writes to a module; see [PC-Link clock](maintenance.md#pc-link-clock).

```yaml
action: nikobus.sync_pc_link_clock
```
