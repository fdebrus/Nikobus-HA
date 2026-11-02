# Importing from your `.nkb` project

The bus tells the integration *what* is installed and *how it's wired* — but not what you **called** things. Your friendly names ("Appliques Salon"), your rooms, and your named scene groupings live only in the Nikobus PC software's project file. The `.nkb` import reads them and applies them to Home Assistant.

> A `.nkb` is the project export from the Nikobus PC software (a ZIP holding the project database). Nothing leaves your machine — it's parsed locally inside Home Assistant.

## Step 1 — Upload the file

*Settings → Devices & Services → Nikobus → **Configure → Upload .nkb project file***. Pick your export (any filename); it's **validated** (it must parse as a real `.nkb`) and saved as `nikobus.nkb` in your config folder. No copying over Samba/SSH.

> Prefer to place it yourself? Drop the file in `/config` as `nikobus.nkb` (or as the only `*.nkb` there) and skip this step.

## Step 2 — Import

You have two ways to apply it:

**Quick path — the button.** Press **3. Import Names from .nkb** on the Bridge device. It **replays the settings you last applied** through the form below, so the two paths always behave the same. If you've never used the form, it imports **everything, non-destructively**: it fills in names/Areas where you haven't set your own, and never clobbers a manual rename.

**Choose what to import.** *Configure → Import from .nkb (choose what)* opens a form where you tick exactly which categories to apply, with an optional **overwrite** toggle. The form **remembers your last-applied choices** and pre-fills them on the next visit:

| Category | What it sets |
|---|---|
| **Device names** | Modules, buttons, IR receivers → `Name (Room)` (the room disambiguates the often-repeated Nikobus names). |
| **Channel names** | The per-output entities you actually toggle — each light / cover / switch (e.g. `Appliques Salon`, `Terrasse`). |
| **Areas** | Each device is placed into a Home Assistant **Area** matching its Nikobus room (created if needed). |
| **Scenes** | Named scene groups — both light scenes and **shutter / master scenes** (see [Scenes](scenes.md)). |
| **Overwrite** *(toggle)* | **Off (default):** suggested only — a name or Area you set by hand always wins. **On:** force the `.nkb` values over your existing names/Areas. |

So you can, for example, refresh just the **channel names** without disturbing the Areas you've already organised, or re-run with **overwrite** after a project change to push the latest names through.

> Re-running is safe and idempotent — unchanged entries are skipped, and (without overwrite) anything you've personalised is left alone.

## What the imported names look like

Device names mirror the Nikobus PC software so the two stay cross-referenceable:

| Device | Imported name | Notes |
|---|---|---|
| Button plate | `7: Porte buanderie (Buanderie)` | The number is the same index the Niko software shows as `BP7`; the room disambiguates repeated names. |
| Unnamed plate | `9: Chambre 2` | A plate the installer never named falls back to its **room**. |
| Plate key (child device) | `Porte buanderie Key 1A` | Each key of a plate is its own HA device; it's named from its parent plate. IR op-points, PC-Logic input keys, and your own renames are never touched. |
| Module | `Dimcontroller (Centrale)` | Modules keep their plain `Name (Room)` — no index prefix. |
| CF scene | `Scene - Soirée` | Matched Central-Function scenes take their real project name. |

**Imported names persist.** They're stored by the integration and re-applied on every start, so they survive restarts, reloads, and re-discovery — without needing Overwrite. A name you set by hand in the HA UI still always wins (unless you explicitly run with Overwrite), and re-importing after the project changed refreshes the imported names.
