# Nikobus for Home Assistant — documentation

The [README](../README.md) covers installation and the quick start. These guides go further:

- [Discovery workflow](discovery.md) — the Bridge device's buttons and sensors, the three discovery steps, and *Customize a module*.
- [Installs without a PC-Link](manual-config.md) — the two JSON files that replace the PC-Link inventory, field by field.
- [Maintenance: backup, verify & clock](maintenance.md) — backing up and verifying the modules' programming, the reprogramming watch, the PC-Link clock.
- [Importing from your `.nkb` project](nkb-import.md) — uploading the project file and importing names, rooms and scenes from it.
- [Buttons, inputs & the entity model](buttons-and-entities.md) — devices and entities per key, press-entity classes, inputs, renaming, audio zones, motion detectors, the RGB controller.
- [Commands on the bus](commands-on-the-bus.md) — switching an output directly or by pressing its key, and what each does to the plate LEDs.
- [Events & automations](events.md) — the `nikobus_*` events, their payload, and automation examples.
- [Scenes](scenes.md) — central-function scenes from the bus, named scenes from the `.nkb`, and your own scene file.
- [Connectivity](connectivity.md) — serial and TCP connections, reconnects, the presence probe, and which port Home Assistant should be on.
- [Services](services.md) — the eight `nikobus.*` actions with their fields.
- [Troubleshooting](troubleshooting.md) — every Repair issue, and the symptoms users report most, with their fix.
- [How the integration works](how-it-works.md) — the library and the integration's modules, how state stays in sync, interoperability.

Reference material — protocol notes, memory maps, the vendor manuals and the code audits — is in [`../documentation/`](../documentation/); what changed in each release is in the [changelog](../CHANGELOG.md).
