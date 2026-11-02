# Maintenance: backup, verify & clock

Three more buttons on the **Nikobus Bridge** device, all **read-only on the bus** except the clock sync. They share the bus with discovery, so they grey out while anything else runs.

## Backup module programming

Every switch, dimmer and roller module keeps its complete programming in its own memory: which button drives which output, in which mode, with which timers. **Backup module programming** (or the `nikobus.backup_modules` action) reads the full memory image of every output module and writes it under `config/nikobus_backup/<timestamp>/`, one `.nkm` file per module plus a `summary.json` with each module's status and checksum. Nothing is written to any module. Run it once after setup and after any change made with the Nikobus PC software, and keep the folder: if a module ever fails, you have its exact programming.

## Verify module programming

Each module can report its status and a checksum it computes over its own memory. **Verify module programming** (or `nikobus.verify_modules`) asks every module for both, reads its memory back and compares. The result lands on the **Programming health** diagnostic sensor (`ok` / `problem` / `unknown`, with per-module detail in the attributes), and a module with an EEPROM error or a checksum mismatch raises a Repair issue naming it. On a healthy installation every checksum matches bit for bit, so a mismatch is real. The module's status reply also carries its family (switch, roller, dimmer); a module that answers as a different family than the one it is stored as is reported as a problem, with a Repair issue, since its entities and link decoding follow the stored type.

## Reprogrammed modules are noticed

Home Assistant records each module's checksum whenever it reads the module's programming — after *2. Load Existing Installation*, *Verify* or *Backup* — and re-reads it ten minutes after start-up and then once a day (one read-only frame per module). A module whose checksum moved was reprogrammed with the Nikobus PC software since its links were last read: a Repair issue asks for a link rescan, the **Programming health** sensor lists the address under `programming_changed`, and the issue clears by itself once the module is read again.

## PC-Link clock

The PC-Link keeps its own clock for the calendar functions of the Nikobus software, and it knows nothing about daylight-saving changes. The **PC-Link clock** diagnostic sensor reads it every hour and exposes `drift_seconds` against Home Assistant's time. **Sync PC-Link clock** (or `nikobus.sync_pc_link_clock`) sets it from Home Assistant's local time — this is the only frame the integration ever writes to a module. Nothing syncs automatically; an automation on the drift covers the DST changes:

```yaml
alias: Nikobus - sync PC-Link clock on drift
triggers:
  - trigger: state
    entity_id: sensor.nikobus_bridge_pc_link_clock
    attribute: drift_seconds
conditions:
  - condition: template
    value_template: "{{ (state_attr('sensor.nikobus_bridge_pc_link_clock', 'drift_seconds') | float(0)) | abs > 120 }}"
actions:
  - action: nikobus.sync_pc_link_clock
```

> The clock sensor's state is the PC-Link's time, so it changes on every hourly read. If that clutters your logbook, exclude `sensor.nikobus_bridge_pc_link_clock` from the logbook in `configuration.yaml`.

## What is deliberately not there

Restoring a backup into a module, and reading a Feedback Module's memory, are not offered. Both require the module's programming mode, which is also its write-enable mode; the integration never enters it. Reprogramming a module is a job for the Nikobus PC software, with the backup as your safety net.
